# Linked fixture: a small bookshop

Two repos for the tests and the eval of linked repos (ADR-027): `backend` (FastAPI) and
`frontend` (TypeScript). The frontend calls the routes of the backend. `labels.yaml` in
`evals/linked/` lists, by hand, every call that reaches a route, and the decoys that must
get no edge: a call to a host outside the project, a call to a route that does not exist,
and calls in test files.
