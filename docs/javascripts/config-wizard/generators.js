// Pure config-output generators. No DOM, no spec knowledge beyond the emit map.

const IMAGE = "ghcr.io/pvliesdonk/markdown-vault-mcp:latest";

// Vars whose value is fixed to a container path in Docker/Compose output.
const CONTAINER_PATHS = {
  MARKDOWN_VAULT_MCP_SOURCE_DIR: "/data/vault",
  MARKDOWN_VAULT_MCP_INDEX_PATH: "/data/state/index.db",
  MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH: "/data/state/embeddings/embeddings",
  MARKDOWN_VAULT_MCP_STATE_PATH: "/data/state/state.json",
  MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR: "/data/state/fastembed",
};

const SECRET_PLACEHOLDER = (key) => `<YOUR_${key.replace(/^MARKDOWN_VAULT_MCP_/, "")}>`;

// Single-quote a value for a POSIX shell `-e KEY=value` argument, but only when
// it contains characters outside the shell-safe set (keeps clean output tidy).
const shellQuote = (v) => {
  const s = String(v);
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`;
};

// Quote a YAML scalar only when it contains characters that would otherwise
// break parsing (or has surrounding whitespace, or is empty).
const yamlScalar = (v) => {
  const s = String(v);
  return /[:#[\]{}&*?|<>=!%@,`"']|^\s|\s$|^$/.test(s) ? JSON.stringify(s) : s;
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

// Build {VAR: value} from the spec + answers. Empty non-secret answers are
// dropped; a visible secret left empty becomes a placeholder so the artifact is
// still complete and signals "replace me".
export function buildEnvMap(spec, answers) {
  const secrets = new Set(spec.secretKeys);
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
      else if (secrets.has(q.var)) map[q.var] = SECRET_PLACEHOLDER(q.var);
    }
  }
  return map;
}

export function isVisible(q, answers) {
  if (!q.showIf) return true;
  return Object.entries(q.showIf).every(([k, allowed]) => allowed.includes(answers[k]));
}

function dockerEnvMap(map) {
  const out = { ...map };
  // SOURCE_DIR is always forced to its container path; the remaining container
  // paths override only when the user supplied a value (else the server default
  // under /data applies).
  out.MARKDOWN_VAULT_MCP_SOURCE_DIR = CONTAINER_PATHS.MARKDOWN_VAULT_MCP_SOURCE_DIR;
  for (const [k, v] of Object.entries(CONTAINER_PATHS)) {
    if (k !== "MARKDOWN_VAULT_MCP_SOURCE_DIR" && k in out) out[k] = v;
  }
  out.FASTMCP_HOME = "/data/state/fastmcp";
  return out;
}

// Quote a .env value when it contains characters that common dotenv parsers
// treat specially (notably `#`, which starts an inline comment in an unquoted
// value and would silently truncate the value on load).
const dotenvQuote = (v) => {
  const s = String(v);
  if (!/[#"'\\\s]/.test(s)) return s;
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
};

export function generateDotenv(map) {
  return Object.entries(map).map(([k, v]) => `${k}=${dotenvQuote(v)}`).join("\n") + "\n";
}

export function generateClaudeJson(map) {
  return JSON.stringify(
    { mcpServers: { "markdown-vault": { command: "markdown-vault-mcp", args: ["serve"], env: map } } },
    null, 2,
  );
}

export function generateDockerRun(map, hostVaultPath) {
  const env = dockerEnvMap(map);
  const lines = [
    "docker run -d --name markdown-vault-mcp",
    "  -p 8000:8000",
    `  -v ${shellQuote(hostVaultPath || "/path/to/vault")}:/data/vault`,
    "  -v state-data:/data/state",
  ];
  for (const [k, v] of Object.entries(env)) lines.push(`  -e ${k}=${shellQuote(v)}`);
  lines.push(`  ${IMAGE}`);
  return lines.join(" \\\n");
}

export function generateCompose(map, hostVaultPath) {
  const env = dockerEnvMap(map);
  const envLines = Object.entries(env).map(([k, v]) => `      ${k}: ${yamlScalar(v)}`).join("\n");
  // Quote the whole "host:/data/vault" mount when the host path needs it.
  const mount = `${hostVaultPath || "/path/to/vault"}:/data/vault`;
  const mountLine = /[\s"#]/.test(mount) ? `      - ${JSON.stringify(mount)}` : `      - ${mount}`;
  return [
    "services:",
    "  markdown-vault-mcp:",
    `    image: ${IMAGE}`,
    "    ports:",
    '      - "8000:8000"',
    "    volumes:",
    mountLine,
    "      - state-data:/data/state",
    "    environment:",
    envLines,
    "volumes:",
    "  state-data:",
  ].join("\n");
}

export function generateSystemd(map) {
  const envLines = Object.entries(map).map(([k, v]) => systemdLine(k, v)).join("\n");
  return [
    "[Unit]",
    "Description=markdown-vault-mcp",
    "After=network.target",
    "",
    "[Service]",
    "Type=simple",
    "# Create this user first: sudo useradd --system --no-create-home markdown-vault-mcp",
    "User=markdown-vault-mcp",
    "ExecStart=/opt/markdown-vault-mcp/venv/bin/markdown-vault-mcp serve --transport http",
    envLines,
    "Restart=on-failure",
    "",
    "[Install]",
    "WantedBy=multi-user.target",
  ].join("\n");
}
