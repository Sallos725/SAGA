BEGIN TRANSACTION;
CREATE TABLE branches (
        world_id TEXT NOT NULL REFERENCES worlds(world_id),
        branch_id TEXT NOT NULL,
        parent_branch_id TEXT,
        fork_event_id TEXT,
        fork_seq INTEGER NOT NULL CHECK(fork_seq >= 0),
        PRIMARY KEY (world_id, branch_id),
        FOREIGN KEY (world_id, parent_branch_id) REFERENCES branches(world_id, branch_id)
    );
INSERT INTO "branches" VALUES('legacy','main',NULL,NULL,0);
INSERT INTO "branches" VALUES('legacy','alternate','main','first',1);
CREATE TABLE entries (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq > 0),
        event_id TEXT NOT NULL,
        idempotency_key TEXT,
        entry_json TEXT NOT NULL,
        PRIMARY KEY (world_id, branch_id, seq),
        UNIQUE (world_id, branch_id, event_id),
        UNIQUE (world_id, branch_id, idempotency_key),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    );
INSERT INTO "entries" VALUES('legacy','main',1,'first',NULL,'{"schema_version":1,"runtime_version":"0.2.0","committed":{"event":{"schema_version":1,"event_id":"first","kind":"attack","actor_id":"player","payload":{"target_id":"goblin","attack_bonus":20,"target_ac":10,"damage":"1d6+2"},"parent_event_id":null,"idempotency_key":null},"seq":1,"state_hash_before":"e9844c1bae9228db5915ea93cb4795e42efc562069c60ab69ed1915e5f555f6e","state_hash_after":"92279c697d3ab7dd2406363b3d998b15b85c7318bb7cbe2b55e2629b1abc9edc"},"result":{"state":{"schema_version":1,"world_id":"legacy","seq":1,"simulated_time":0,"actors":{"player":{"actor_id":"player","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}},"goblin":{"actor_id":"goblin","hp":4,"max_hp":10,"inventory":{},"conditions":[],"stats":{}}},"flags":{},"quest_state":{}},"facts":["player hits goblin (21 vs AC 10) for 6 damage; HP 10->4."],"rolls":[{"roll_key":"attack","expression":"1d20+20","raw":[1],"modifier":20,"total":21},{"roll_key":"damage","expression":"1d6+2","raw":[4],"modifier":2,"total":6}],"patches":[{"path":"actors.goblin.hp","before":10,"after":4}],"trace":["generic-fantasy.attack: hit"],"resolution":null}}');
INSERT INTO "entries" VALUES('legacy','alternate',1,'first',NULL,'{"schema_version":1,"runtime_version":"0.2.0","committed":{"event":{"schema_version":1,"event_id":"first","kind":"attack","actor_id":"player","payload":{"target_id":"goblin","attack_bonus":20,"target_ac":10,"damage":"1d6+2"},"parent_event_id":null,"idempotency_key":null},"seq":1,"state_hash_before":"e9844c1bae9228db5915ea93cb4795e42efc562069c60ab69ed1915e5f555f6e","state_hash_after":"92279c697d3ab7dd2406363b3d998b15b85c7318bb7cbe2b55e2629b1abc9edc"},"result":{"state":{"schema_version":1,"world_id":"legacy","seq":1,"simulated_time":0,"actors":{"player":{"actor_id":"player","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}},"goblin":{"actor_id":"goblin","hp":4,"max_hp":10,"inventory":{},"conditions":[],"stats":{}}},"flags":{},"quest_state":{}},"facts":["player hits goblin (21 vs AC 10) for 6 damage; HP 10->4."],"rolls":[{"roll_key":"attack","expression":"1d20+20","raw":[1],"modifier":20,"total":21},{"roll_key":"damage","expression":"1d6+2","raw":[4],"modifier":2,"total":6}],"patches":[{"path":"actors.goblin.hp","before":10,"after":4}],"trace":["generic-fantasy.attack: hit"],"resolution":null}}');
CREATE TABLE snapshots (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq >= 0),
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        PRIMARY KEY (world_id, branch_id, seq),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    );
INSERT INTO "snapshots" VALUES('legacy','main',0,'{"schema_version":1,"world_id":"legacy","seq":0,"simulated_time":0,"actors":{"player":{"actor_id":"player","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}},"goblin":{"actor_id":"goblin","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}}},"flags":{},"quest_state":{}}','e9844c1bae9228db5915ea93cb4795e42efc562069c60ab69ed1915e5f555f6e');
INSERT INTO "snapshots" VALUES('legacy','main',1,'{"schema_version":1,"world_id":"legacy","seq":1,"simulated_time":0,"actors":{"player":{"actor_id":"player","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}},"goblin":{"actor_id":"goblin","hp":4,"max_hp":10,"inventory":{},"conditions":[],"stats":{}}},"flags":{},"quest_state":{}}','92279c697d3ab7dd2406363b3d998b15b85c7318bb7cbe2b55e2629b1abc9edc');
INSERT INTO "snapshots" VALUES('legacy','alternate',1,'{"schema_version":1,"world_id":"legacy","seq":1,"simulated_time":0,"actors":{"player":{"actor_id":"player","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}},"goblin":{"actor_id":"goblin","hp":4,"max_hp":10,"inventory":{},"conditions":[],"stats":{}}},"flags":{},"quest_state":{}}','92279c697d3ab7dd2406363b3d998b15b85c7318bb7cbe2b55e2629b1abc9edc');
CREATE TABLE worlds (
        world_id TEXT PRIMARY KEY,
        initial_state_json TEXT NOT NULL,
        seed TEXT NOT NULL,
        ruleset TEXT NOT NULL,
        ruleset_version TEXT NOT NULL,
        runtime_version TEXT NOT NULL
    );
INSERT INTO "worlds" VALUES('legacy','{"schema_version":1,"world_id":"legacy","seq":0,"simulated_time":0,"actors":{"player":{"actor_id":"player","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}},"goblin":{"actor_id":"goblin","hp":10,"max_hp":10,"inventory":{},"conditions":[],"stats":{}}},"flags":{},"quest_state":{}}','dev-seed','generic-fantasy','0.1.0','0.2.0');
COMMIT;
PRAGMA user_version = 1;
