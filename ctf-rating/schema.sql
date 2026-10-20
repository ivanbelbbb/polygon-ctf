-- Схема рейтинга CTF-полигона (Postgres).
-- Правило: одно решение на задачу на команду за неделю, очки копятся по сезону.

CREATE TABLE teams (
  id         int PRIMARY KEY,                 -- номер команды (namespace team-N)
  name       text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE participants (
  id       serial PRIMARY KEY,
  team_id  int NOT NULL REFERENCES teams(id),
  slot     int NOT NULL,                      -- p1..p4 из имени деплоймента
  nickname text,
  UNIQUE (team_id, slot)
);

CREATE TABLE tasks (
  name   text PRIMARY KEY,                    -- совпадает с name в tasks.yaml
  track  text NOT NULL DEFAULT 'web',
  level  int  NOT NULL DEFAULT 1,
  points int  NOT NULL DEFAULT 100
);
INSERT INTO tasks (name, track, level, points) VALUES ('level1', 'web', 1, 100);

-- Засчитанные решения. participant_id = кто из команды решил первым (индивидуальный зачёт).
CREATE TABLE solves (
  id             bigserial PRIMARY KEY,
  team_id        int  NOT NULL REFERENCES teams(id),
  participant_id int  NOT NULL REFERENCES participants(id),
  task           text NOT NULL REFERENCES tasks(name),
  week_start     date NOT NULL,               -- понедельник недели решения (UTC)
  points         int  NOT NULL,               -- очки фиксируются на момент решения
  solved_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (team_id, task, week_start)          -- второе решение той же командой игнорируется
);

-- Все запросы к чекеру, включая отклонённые (аудит и разбор читерства).
CREATE TABLE events (
  id          bigserial PRIMARY KEY,
  received_at timestamptz NOT NULL DEFAULT now(),
  source_ip   text,
  payload     jsonb NOT NULL,
  accepted    boolean NOT NULL,
  reason      text
);
CREATE INDEX events_received_idx ON events (received_at);

CREATE VIEW leaderboard_teams AS
SELECT t.id AS team_id,
       COALESCE(NULLIF(t.name, ''), 'team-' || t.id) AS name,
       COALESCE(SUM(s.points), 0)::int AS points,
       COUNT(s.id)::int AS solves,
       MAX(s.solved_at) AS last_solve
FROM teams t LEFT JOIN solves s ON s.team_id = t.id
GROUP BY t.id, t.name;

CREATE VIEW leaderboard_participants AS
SELECT p.id AS participant_id, p.team_id, p.slot,
       COALESCE(NULLIF(p.nickname, ''), 'team-' || p.team_id || '/p' || p.slot) AS name,
       COALESCE(SUM(s.points), 0)::int AS points,
       COUNT(s.id)::int AS solves,
       MAX(s.solved_at) AS last_solve
FROM participants p LEFT JOIN solves s ON s.participant_id = p.id
GROUP BY p.id;
