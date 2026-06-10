import { createClient } from "@libsql/client";
import { drizzle } from "drizzle-orm/libsql";

const url = process.env.TURSO_DB_URL || process.env.DATABASE_URL;

if (!url) {
  throw new Error("TURSO_DB_URL is required");
}

const client = createClient({
  url,
  authToken: process.env.TURSO_AUTH_TOKEN
});

export const db = drizzle(client);
