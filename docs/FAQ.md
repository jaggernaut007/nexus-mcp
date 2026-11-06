# FAQ

The site FAQ (`site/index.html`, visible and JSON-LD) gives the same answers in a shorter form.

## How do I test Nexus-MCP on my own code before I install it?

Run `uvx nexus-mcp-ci eval` in the project folder. It is free, runs offline and calls no LLM.
It compares Nexus-MCP with Grep and Read on about 30 questions made from your code and prints
"Strong fit", "Likely fit" or "Weak fit". See [EVALUATE.md](EVALUATE.md).

Measured: Chromium `base/` (3,638 files) 950 against 17,998 median tokens to reach the right
file; pallets/click (91 files) 1,301 against 24,774; the Nexus-MCP source (59 files) 1,234 against 5,385.

## Does my code leave my machine?

No. The server makes no network calls and has no telemetry. The package and, once, the
embedding model are downloaded; set `NEXUS_OFFLINE=1` after that. Only `eval --agent` (through
your own Claude or Codex CLI) and `eval share` (numbers only) send anything, and both ask
first. See [PRIVACY.md](PRIVACY.md).

## Where is my data, and how do I remove it?

The index, memories and graph are in `<project>/.nexus/`. Run `nexus-mcp-ci data where` to list
every location and `nexus-mcp-ci data clear --all --yes` to remove them.

## Does it lower token use?

On the projects we measured, yes. In live runs with Claude Sonnet, cost fell by 22% on a
private 460-file project and by 3% on django (inside the run-to-run noise). The saving is
large on code the model does not know and small on code it knows from training. See the
[benchmarks page](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html).

## Which clients does it work with?

Any MCP client with the stdio transport: Claude Code, Claude Desktop, Codex, Cursor, Windsurf,
Cline, Continue and Goose.

## Which languages are supported?

12 languages get symbols and search: Python, JavaScript, TypeScript, Go, Java, Rust, C, C++, C# and PHP, Bash and Kotlin.
All but Bash and Kotlin also get graph nodes and static call edges.

## How does it compare with Serena and claude-context?

Serena finds code through language servers and can edit code. claude-context runs hybrid search
over a vector database with an embedding provider. Nexus-MCP is read only and fully local, with
a static call graph and project memory. Measure it on your repository instead of relying on a
comparison.

## Is it free?

Yes, for noncommercial use, under the PolyForm Noncommercial 1.0.0 license. Commercial use
needs a separate license from the maintainer.
