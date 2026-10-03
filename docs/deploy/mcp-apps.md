---
description: "What an MCP App of Markdown Vault MCP is, which clients render it, and the one setting an operator may need."
kind: how-to
---

# MCP Apps

An MCP App is an interactive HTML interface, a form, a view or a dashboard, that a client renders inside the conversation instead of showing a tool's text. Markdown Vault MCP ships one. A tool declares the interface it wants rendered; the client fetches that interface from the server as a resource and shows it in a frame it sandboxes; from there the interface calls the server's tools. What those tools reach is the [security model](../security-model.md): an app runs with the caller's access, nothing more.

## Which clients render it

Host support is an extension to MCP and varies by client. The protocol's own list of hosts that render MCP Apps names Claude on the web and Claude Desktop, VS Code GitHub Copilot, Microsoft 365 Copilot, Goose and Postman among others; the [client matrix](https://modelcontextprotocol.io/extensions/client-matrix) is the current list.

A client without the extension still works with the server. It gets each tool's text result, and the tools that exist only for the interface stay out of its view. They are registered with `visibility=["app"]`; the model never sees them; the [tools reference](../reference/tools/index.md) marks them `Visible to: app.`. A tool that opens the interface for an app-capable client and returns a text summary elsewhere decides between the two with the client's advertised capabilities; this server's own tools say which they do.

## The operator setting

The interface is loaded from the server's own resource, `ui://markdown_vault_mcp/app.html`, and rendered in a frame the client sandboxes. `MARKDOWN_VAULT_MCP_APP_DOMAIN` is the setting through which an operator names the domain the client uses for that sandbox. Leave it unset and the host of `MARKDOWN_VAULT_MCP_BASE_URL` serves; set it when the host a client sees differs from `BASE_URL`, such as behind a proxy that changes the apparent hostname. The setting is read by this server's own code when it configures the resource, in the block named below. The [configuration reference](../reference/configuration.md#mcp-apps) lists the variable with the rest.

## Where the interface comes from

The app shell is `src/markdown_vault_mcp/static/app.html`, built from `app.src.html` by `scripts/vendor_spa.py`, which embeds the JavaScript libraries the interface needs so the client loads nothing from a content network at run time. The `DOMAIN-APP-RESOURCE` block of `src/markdown_vault_mcp/_server_apps.py` is where the project configures the resource: the content-security policy a client enforces, and the sandbox domain it resolves from `APP_DOMAIN` or `BASE_URL`. Both are a contributor's concern; [Contribute](../contribute/index.md) has the development setup. The shell is rebuilt whenever `app.src.html` changes, and after a template update.

<!-- DOMAIN-MCP-APPS-EXTRA-START -->
<!-- What this server's app shows and which tool opens it; a view that needs a particular client; kept across copier update. -->
<!-- DOMAIN-MCP-APPS-EXTRA-END -->
