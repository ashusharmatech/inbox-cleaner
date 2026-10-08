-- Run this in Supabase → SQL Editor
create table if not exists accounts (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  refresh_token_enc text not null,
  scan_status text not null default 'idle',   -- idle | scanning | done | error
  scanned int not null default 0,
  total int not null default 0,
  scan_error text,
  last_scanned_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists senders (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references accounts(id) on delete cascade,
  from_email text not null,
  from_name text,
  msg_count int not null default 0,
  unread_count int not null default 0,
  last_received timestamptz,
  list_unsubscribe text,
  one_click boolean not null default false,
  status text not null default 'active',      -- active | unsubscribed | kept | deleting | deleted
  unique (account_id, from_email)
);

create index if not exists senders_account_idx on senders(account_id, msg_count desc);

-- The backend uses the service-role key, which bypasses RLS.
-- Enabling RLS with no policies blocks everyone else (anon key) completely.
alter table accounts enable row level security;
alter table senders enable row level security;
