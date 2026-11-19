-- E4a stand: the examples database of Superset 6.1.0 next to the one of 4.1.2 (POSTGRES_DB).
-- Statement logging is switched on per database after `superset load-examples` (stand_up.sh),
-- so the log holds what the engine ran for the research, not the load.
CREATE DATABASE examples_61 OWNER examples;
