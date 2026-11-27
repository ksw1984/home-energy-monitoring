<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.

OpenCode puts the MCP server's key in front of each tool's name, so it lists this tool as `codegraph_codegraph_explore` where other agents list `codegraph_explore`; if the server is registered under another key, that key replaces the first `codegraph_`.
<!-- CODEGRAPH_END -->

## Repository rule

This repository is indexed with CodeGraph. For code lookup, call flows, callers/callees, impact analysis, or "where is X" questions, use `codegraph_codegraph_explore` first (or `codegraph explore "<symbols or question>"` from a shell). Treat returned source as already read. Fall back to grep/glob/read only for non-code files or when CodeGraph cannot answer. After code edits, run `codegraph sync .` if auto-sync has not updated the graph.
