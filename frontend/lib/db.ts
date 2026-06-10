import { createClient } from "@libsql/client";
import { drizzle } from "drizzle-orm/libsql";

type Db = ReturnType<typeof drizzle>;
let _db: Db | undefined;

export function getDb(): Db {
  if (!_db) {
    const url = process.env.TURSO_DB_URL || process.env.DATABASE_URL;
    if (!url) throw new Error("TURSO_DB_URL is required");
    _db = drizzle(createClient({ url, authToken: process.env.TURSO_AUTH_TOKEN }));
  }
  return _db;
}
