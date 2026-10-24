-- Stand-ins for three clients' Supabase projects (demo profile only).
-- Fictional data; the password is a local-only placeholder.

CREATE DATABASE acme_dev;
CREATE DATABASE acme_prod;
CREATE DATABASE globex_dev;

\connect acme_dev
CREATE TABLE customers (id serial PRIMARY KEY, name text NOT NULL, plan text NOT NULL);
INSERT INTO customers (name, plan) VALUES ('Road Runner Inc', 'trial'), ('Coyote LLC', 'pro');

\connect acme_prod
CREATE TABLE customers (id serial PRIMARY KEY, name text NOT NULL, plan text NOT NULL);
INSERT INTO customers (name, plan)
VALUES ('Road Runner Inc', 'pro'), ('Coyote LLC', 'pro'), ('Tunnel Paint Co', 'enterprise');

\connect globex_dev
CREATE TABLE partners (id serial PRIMARY KEY, name text NOT NULL);
INSERT INTO partners (name) VALUES ('Hank Scorpio Holdings');
