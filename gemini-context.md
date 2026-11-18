# Using nexus-mcp

The server instructions and the tool descriptions say which tool answers which question.
Three more points:

- Put all the names you need in one `find_symbol` call, not one call or read per name.
- With linked repos (`nexus-mcp-ci link`), `search` and `find_symbol` also look in them, and a path
  starts with the repo name. `graph` follows HTTP calls between repos: a hop that ends with
  `via GET /orders/{}` is a client call to a route. Use `transitive=true` for the impact across repos.
- A `warning` field means a background reindex runs, so a result can be a little old.
- For a project of a few thousand lines, or files outside the indexed path, read the
  files directly.
