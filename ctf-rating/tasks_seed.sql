-- Справочник задач. Идемпотентный: можно запускать повторно (обновляет очки и порядок).
-- name должен совпадать с name в pipeline/tasks.yaml, иначе чекер ответит "unknown task".
-- Очки растут с уровнем: 50 / 100 / 150 / 200 / 250 / 300 - при необходимости поменяйте.
INSERT INTO tasks (name, track, level, points) VALUES
  ('level0', 'web', 0,  50),
  ('level1', 'web', 1, 100),
  ('level2', 'web', 2, 150),
  ('level3', 'web', 3, 200),
  ('level4', 'web', 4, 250),
  ('level5', 'web', 5, 300)
ON CONFLICT (name) DO UPDATE
  SET track = EXCLUDED.track, level = EXCLUDED.level, points = EXCLUDED.points;
