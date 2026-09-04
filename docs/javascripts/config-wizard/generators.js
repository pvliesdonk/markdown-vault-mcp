// Pure config-output generators. No DOM. All per-project data comes from
// spec.meta; Docker path behaviour comes from per-question dockerVolume /
// dockerPath. This file is template-owned and identical across all consumers.

const FASTMCP_HOME = "/data/state/fastmcp";
// The named state volume, mounted in every Docker/Compose artifact.
const STATE_VOLUME = "state-data:/data/state";

const secretPlaceholder = (key, envPrefix) => {
  const prefix = `${envPrefix}_`;
  const short = key.startsWith(prefix) ? key.slice(prefix.length) : key;
  return `<YOUR_${short}>`;
};

// Single-quote a value for a POSIX shell `-e KEY=value` argument, but only when
// it contains characters outside the shell-safe set (keeps clean output tidy).
const shellQuote = (v) => {
  const s = String(v);
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`;
};

// Bare tokens that YAML 1.1 parsers (used by Docker Compose) coerce to
// bool/null/number. Env values are always strings, so these must be quoted to
// stay strings — `KEY: true` would otherwise load as a boolean, `KEY: 8080` as
// an int. Case-insensitive; covers bool, null, decimal/hex/octal ints, floats
// (incl. exponent and ±.inf/.nan).
const YAML_TYPED =
  /^(?:y|n|yes|no|on|off|true|false|null|~|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|0x[0-9a-fA-F]+|0o[0-7]+|[-+]?\.(?:inf|nan))$/i;

// Quote a YAML scalar when it contains characters that would otherwise break
// parsing (or has surrounding whitespace, or is empty), or when it is a bare
// token a YAML 1.1 parser would type-coerce.
const yamlScalar = (v) => {
  const s = String(v);
  return YAML_TYPED.test(s) || /[\n:#[\]{}&*?|<>=!%@,`"']|^\s|\s$|^$/.test(s)
    ? JSON.stringify(s)
    : s;
};

// A systemd `Environment=` line. systemd does NOT expand `$` in Environment=
// values (expansion only happens in ExecXYZ= directives), so `$` is left
// literal. It DOES resolve `%` specifiers (escaped as `%%`) and process
// C-style `\`/`"` escapes (systemd/systemd#36488), so those are escaped, and
// the assignment is wrapped in quotes when it contains whitespace, a quote, or
// a backslash.
const systemdLine = (k, v) => {
  const s = String(v).replace(/%/g, "%%");
  if (!/[\s"\\]/.test(s)) return `Environment=${k}=${s}`;
  const escaped = s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `Environment="${k}=${escaped}"`;
};

// Validate a loaded wizard spec before any generator runs. The generators
// iterate spec.questions and dereference spec.meta.{projectName,dockerImage,
// envPrefix} with no per-field guard at the use site, so an incomplete spec
// (hand-edited, or served mid-migration) must fail loudly here rather than
// reach the generators and emit
// undefined-laden artifacts the user copy-pastes. The JSON Schema enforces the
// same shape, but only in the Python test lane — never at runtime, which is what
// this guards. Throws Error with a specific message on the first violation.
export function validateSpec(spec) {
  if (!spec || typeof spec !== "object" || !Array.isArray(spec.questions)) {
    throw new Error("wizard-spec.json is malformed (missing questions array)");
  }
  if (!spec.meta || typeof spec.meta !== "object") {
    throw new Error("wizard-spec.json is malformed (missing meta block)");
  }
  for (const k of ["projectName", "dockerImage", "envPrefix"]) {
    if (typeof spec.meta[k] !== "string" || spec.meta[k] === "") {
      throw new Error(`wizard-spec.json is malformed (meta.${k} missing or empty)`);
    }
  }
}

// Build {VAR: value} from the spec + answers. Empty non-secret answers are
// dropped; a visible secret left empty becomes a placeholder so the artifact is
// still complete and signals "replace me".
export function buildEnvMap(spec, answers) {
  const secrets = new Set(spec.secretKeys ?? []);
  const envPrefix = spec.meta.envPrefix;
  const map = {};
  for (const q of spec.questions) {
    if (!isVisible(q, answers)) continue;
    if (q.options) {
      const chosen = q.options.find((o) => o.value === answers[q.id]);
      if (chosen && chosen.emit) Object.assign(map, chosen.emit);
    }
    if (q.var) {
      const raw = answers[q.id];
      if (raw !== undefined && raw !== "") map[q.var] = raw;
      else if (secrets.has(q.var)) map[q.var] = secretPlaceholder(q.var, envPrefix);
    }
  }
  return map;
}

export function isVisible(q, answers) {
  if (!q.showIf) return true;
  return Object.entries(q.showIf).every(([k, allowed]) => allowed.includes(answers[k]));
}

// A dockerVolume question contributes only when the user actually supplied a
// host path. Blank means "I am not using this", and the two halves must agree:
// emitting the mount without the env override leaves a bind mount nothing
// references, and emitting the override without a real host path points the
// server at a file that does not exist. Both halves therefore key off the same
// answer.
//
// This replaced a `/path/to/<id>` placeholder that a blank answer used to
// produce as a "replace me" marker. That reads fine for a path the server
// requires, and breaks a path it merely accepts: leaving the optional bearer
// tokens file blank produced `-e ..._BEARER_TOKENS_FILE=/etc/<project>/
// tokens.toml` beside `BEARER_TOKEN`, and core prefers a tokens file whenever
// both are set — so the default bearer output named a file that was never
// mounted and refused to start. The spec has no required-ness signal to
// distinguish the two cases, so the safe rule is the one that cannot generate
// a broken command.
const dockerHostPath = (q, answers) => (q.dockerVolume ? answers[q.id] : "");

// Bind mounts for visible, answered dockerVolume questions:
// [[hostPath, containerPath]].
export function dockerVolumes(spec, answers) {
  const vols = [];
  for (const q of spec.questions) {
    if (!isVisible(q, answers)) continue;
    const host = dockerHostPath(q, answers);
    if (host) vols.push([host, q.dockerVolume]);
  }
  return vols;
}

// Rewrite the env map for Docker/Compose output. A dockerVolume question's var
// is fixed to its container path once the user supplies a host path, because
// the bind mount above then makes that path real. A dockerPath question's var
// is fixed only when already present (the user opted into the value);
// dockerPath never adds a mount. FASTMCP_HOME is injected.
function dockerEnvMap(spec, answers, map) {
  const out = { ...map };
  for (const q of spec.questions) {
    if (!isVisible(q, answers) || !q.var) continue;
    if (dockerHostPath(q, answers)) {
      out[q.var] = q.dockerVolume;
    } else if (q.dockerPath && q.var in out) {
      out[q.var] = q.dockerPath;
    }
  }
  out.FASTMCP_HOME = FASTMCP_HOME;
  return out;
}

// Quote a .env value when it contains characters that common dotenv parsers
// treat specially (notably `#`, which starts an inline comment in an unquoted
// value and would silently truncate the value on load, and `$`, which triggers
// variable expansion when the file is shell-sourced).
const dotenvQuote = (v) => {
  const s = String(v);
  if (!/[#"'\\\s$]/.test(s)) return s;
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\$/g, '\\$')}"`;
};

export function generateDotenv(map) {
  return Object.entries(map).map(([k, v]) => `${k}=${dotenvQuote(v)}`).join("\n") + "\n";
}

export function generateClaudeJson(meta, map) {
  return JSON.stringify(
    { mcpServers: { [meta.projectName]: { command: meta.projectName, args: ["serve"], env: map } } },
    null, 2,
  );
}

export function generateDockerRun(spec, answers, map) {
  const env = dockerEnvMap(spec, answers, map);
  const lines = [
    `docker run -d --name ${spec.meta.projectName}`,
    "  -p 8000:8000",
    `  -v ${STATE_VOLUME}`,
  ];
  for (const [host, container] of dockerVolumes(spec, answers)) {
    lines.push(`  -v ${shellQuote(host)}:${container}`);
  }
  for (const [k, v] of Object.entries(env)) lines.push(`  -e ${k}=${shellQuote(v)}`);
  lines.push(`  ${spec.meta.dockerImage}`);
  return lines.join(" \\\n");
}

export function generateCompose(spec, answers, map) {
  const env = dockerEnvMap(spec, answers, map);
  const volLines = [`      - ${STATE_VOLUME}`];
  for (const [host, container] of dockerVolumes(spec, answers)) {
    volLines.push(`      - ${yamlScalar(`${host}:${container}`)}`);
  }
  const envLines = Object.entries(env).map(([k, v]) => `      ${k}: ${yamlScalar(v)}`).join("\n");
  return [
    "services:",
    `  ${spec.meta.projectName}:`,
    `    image: ${spec.meta.dockerImage}`,
    "    ports:",
    '      - "8000:8000"',
    "    volumes:",
    ...volLines,
    "    environment:",
    envLines,
    "volumes:",
    "  state-data:",
  ].join("\n");
}

export function generateSystemd(meta, map) {
  const name = meta.projectName;
  const envLines = Object.entries(map).map(([k, v]) => systemdLine(k, v)).join("\n");
  return [
    "[Unit]",
    `Description=${name}`,
    "After=network.target",
    "",
    "[Service]",
    "Type=simple",
    `# Create this user first: sudo useradd --system --no-create-home ${name}`,
    `User=${name}`,
    `ExecStart=/opt/${name}/venv/bin/${name} serve --transport http`,
    envLines,
    "Restart=on-failure",
    "",
    "[Install]",
    "WantedBy=multi-user.target",
  ].join("\n");
}
