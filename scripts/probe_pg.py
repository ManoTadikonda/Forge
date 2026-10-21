"""One-off environment probe: confirm the embedded PostgreSQL server starts."""

import pathlib

import pgserver

data_dir = pathlib.Path("/tmp/forge-pgprobe/pgdata")
data_dir.mkdir(parents=True, exist_ok=True)

server = pgserver.get_server(data_dir)
print("URI:", server.get_uri())
print(server.psql("select version();"))
