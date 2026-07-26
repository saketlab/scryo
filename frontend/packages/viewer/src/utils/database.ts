// Copyright (c) 2025 Apple Inc. Licensed under MIT License.

import { Coordinator, restConnector, socketConnector, wasmConnector, type Selection } from "@uwdata/mosaic-core";
import * as SQL from "@uwdata/mosaic-sql";

import { createDuckDB } from "./duckdb.js";

/** Initialize the database connector for a Mosaic coordinator */
export async function initializeDatabase(
  coordinator: Coordinator,
  type: "wasm" | "socket" | "rest",
  uri: string | null | undefined = undefined,
) {
  // building this outside the branch cost a 33 MB download on every rest/socket load
  if (type == "wasm") {
    const db = await createDuckDB();
    const conn = await wasmConnector({ duckdb: db.duckdb, connection: db.connection });
    coordinator.databaseConnector(conn);
  } else if (type == "socket") {
    const conn = await socketConnector({ uri: uri ?? "" });
    coordinator.databaseConnector(conn);
  } else if (type == "rest") {
    const conn = await restConnector({ uri: uri ?? "" });
    coordinator.databaseConnector(conn);
  }
}

/** Convert a Mosaic predicate to SQL string */
export function predicateToString(predicate: ReturnType<Selection["predicate"]>): string | null {
  if (predicate == null) {
    return null;
  }
  if (predicate instanceof Array) {
    if (predicate.length == 0) {
      return null;
    }
    return SQL.and(predicate).toString().trim();
  }
  if (typeof predicate == "string") {
    return predicate.trim();
  }
  if (typeof predicate == "boolean") {
    return SQL.literal(predicate).toString();
  }
  return predicate.toString().trim();
}

export function resolveSQLTemplate(template: string, vars: Record<string, string>): string {
  return template.replace(/\$([a-zA-Z][a-zA-Z0-9\_]+)/g, (original, name) => {
    if (vars[name] != undefined) {
      return vars[name];
    } else {
      return original;
    }
  });
}

/** Column description */
export interface ColumnDesc {
  name: string;
  type: string;
  jsType: JSType | null;
}

export interface EmbeddingLegend {
  indexColumn: string;
  legend: {
    label: string;
    color: string;
    predicate: any;
    count: number;
  }[];
  /** Full color palette for all category indices. */
  categoryColors?: string[];
  /** Whether this is a continuous color scale (gradient bar legend instead of dots). */
  isContinuous?: boolean;
}

export async function columnDescriptions(coordinator: Coordinator, table: string): Promise<ColumnDesc[]> {
  let result = Array.from(await coordinator.query(`DESCRIBE ${table}`));
  return result.map((column) => ({
    name: column.column_name,
    type: column.column_type,
    jsType: jsTypeFromDBType(column.column_type),
  }));
}

export async function distinctCount(coordinator: Coordinator, table: string, column: string): Promise<number> {
  let r = await coordinator.query(`SELECT COUNT(DISTINCT ${SQL.column(column)}) AS count FROM ${table}`);
  return r.get(0).count;
}

export async function distinctCounts(
  coordinator: Coordinator,
  table: string,
  columns: string[],
): Promise<Map<string, number>> {
  if (columns.length == 0) {
    return new Map();
  }
  let alias = (index: number) => `distinct_${index}`;
  try {
    let select = columns.map((c, i) => `COUNT(DISTINCT ${SQL.column(c)}) AS ${SQL.column(alias(i))}`).join(", ");
    let row = (await coordinator.query(`SELECT ${select} FROM ${table}`)).get(0);
    return new Map(columns.map((c, i) => [c, Number(row[alias(i)])]));
  } catch {
    // one unsupported type fails the whole batch
    let counts = await Promise.all(columns.map((c) => distinctCount(coordinator, table, c)));
    return new Map(columns.map((c, i) => [c, counts[i]]));
  }
}

export type JSType = "string" | "number" | "string[]" | "Date";

export function jsTypeFromDBType(dbType: string): JSType | null {
  if (numberTypes.has(dbType)) {
    return "number";
  } else if (stringTypes.has(dbType)) {
    return "string";
  } else if (dateTypes.has(dbType)) {
    return "Date";
  } else if (dbType.match(/^(VARCHAR|TEXT)\[\d*\]$/)) {
    return "string[]";
  } else if (dbType.startsWith("ENUM(")) {
    // DuckDB maps pandas Categorical to ENUM; treat it as a string so "Color by" lists it
    return "string";
  } else {
    return null;
  }
}

const numberTypes = new Set([
  "REAL",
  "FLOAT4",
  "FLOAT8",
  "FLOAT",
  "DOUBLE",
  "INT",
  "TINYINT",
  "INT1",
  "SMALLINT",
  "INT2",
  "SHORT",
  "INTEGER",
  "INT4",
  "INT",
  "SIGNED",
  "INT8",
  "LONG",
  "BIGINT",
  "UTINYINT",
  "USMALLINT",
  "UINTEGER",
  "UBIGINT",
  "UHUGEINT",
]);

const stringTypes = new Set(["BOOLEAN", "VARCHAR", "CHAR", "BPCHAR", "TEXT", "STRING"]);

const dateTypes = new Set([
  "DATE",
  "TIME",
  "DATETIME",
  "TIMESTAMP",
  "TIMESTAMPTZ",
  "TIMESTAMP WITH TIME ZONE",
  "TIMESTAMP WITHOUT TIME ZONE",
]);
