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

const SECRET_PLACEHOLDER = (key) => `<YOUR_${key.replace(/^.*?_?([A-Z_]+)$/, "$1")}>`;

// Build {VAR: value} from the spec + answers. Empty answers are dropped; empty
// secrets become placeholders so the artifact is still complete.
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
      else if (secrets.has(q.var) && answers[q.id] === "") map[q.var] = SECRET_PLACEHOLDER(q.var);
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
  for (const [k, v] of Object.entries(CONTAINER_PATHS)) {
    if (k in out || k === "MARKDOWN_VAULT_MCP_SOURCE_DIR") out[k] = v;
  }
  out.FASTMCP_HOME = "/data/state/fastmcp";
  return out;
}

export function generateDotenv(map) {
  return Object.entries(map).map(([k, v]) => `${k}=${v}`).join("\n") + "\n";
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
    `  -v ${hostVaultPath || "/path/to/vault"}:/data/vault`,
    "  -v state-data:/data/state",
  ];
  for (const [k, v] of Object.entries(env)) lines.push(`  -e ${k}=${v}`);
  lines.push(`  ${IMAGE}`);
  return lines.join(" \\\n");
}

export function generateCompose(map, hostVaultPath) {
  const env = dockerEnvMap(map);
  const envLines = Object.entries(env).map(([k, v]) => `      ${k}: ${v}`).join("\n");
  return [
    "services:",
    "  markdown-vault-mcp:",
    `    image: ${IMAGE}`,
    "    ports:",
    '      - "8000:8000"',
    "    volumes:",
    `      - ${hostVaultPath || "/path/to/vault"}:/data/vault`,
    "      - state-data:/data/state",
    "    environment:",
    envLines,
    "volumes:",
    "  state-data:",
  ].join("\n");
}

export function generateSystemd(map) {
  const envLines = Object.entries(map).map(([k, v]) => `Environment=${k}=${v}`).join("\n");
  return [
    "[Unit]",
    "Description=markdown-vault-mcp",
    "After=network.target",
    "",
    "[Service]",
    "Type=simple",
    "ExecStart=/opt/markdown-vault-mcp/venv/bin/markdown-vault-mcp serve --transport http",
    envLines,
    "Restart=on-failure",
    "",
    "[Install]",
    "WantedBy=multi-user.target",
  ].join("\n");
}
