// Copyright (c) 2025 Apple Inc. Licensed under MIT License.

import type { Coordinator } from "@uwdata/mosaic-core";
import * as SQL from "@uwdata/mosaic-sql";
import * as d3 from "d3";

import { distinctCount, jsTypeFromDBType } from "../../utils/database.js";
import { computeFieldStats } from "../common/aggregate.js";
import { inferBinning, inferTimeBinning, type Binning } from "../common/binning.js";
import { inferNumberFormatter, inferTimeFormatter } from "../common/formatter.js";
import { type ChartTheme } from "../common/theme.js";

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
  /** Whether this is a continuous color scale. */
  isContinuous?: boolean;
  /** The color scale name used (for continuous mode). */
  colorScale?: string;
  /** Data min/max (for continuous mode, to rebuild legend without re-querying). */
  dataRange?: { min: number; max: number };
}

/** Available continuous color scales. */
export const CONTINUOUS_SCALES: { name: string; label: string; category: "sequential" | "diverging" }[] = [
  { name: "viridis", label: "Viridis", category: "sequential" },
  { name: "plasma", label: "Plasma", category: "sequential" },
  { name: "inferno", label: "Inferno", category: "sequential" },
  { name: "magma", label: "Magma", category: "sequential" },
  { name: "cividis", label: "Cividis", category: "sequential" },
  { name: "turbo", label: "Turbo", category: "sequential" },
  { name: "blues", label: "Blues", category: "sequential" },
  { name: "greens", label: "Greens", category: "sequential" },
  { name: "reds", label: "Reds", category: "sequential" },
  { name: "purples", label: "Purples", category: "sequential" },
  { name: "ylgnbu", label: "YlGnBu", category: "sequential" },
  { name: "ylorbr", label: "YlOrBr", category: "sequential" },
  { name: "spectral", label: "Spectral", category: "diverging" },
  { name: "rdylbu", label: "RdYlBu", category: "diverging" },
  { name: "rdylgn", label: "RdYlGn", category: "diverging" },
  { name: "rdbu", label: "RdBu", category: "diverging" },
  { name: "piyg", label: "PiYG", category: "diverging" },
  { name: "prgn", label: "PRGn", category: "diverging" },
  { name: "brbg", label: "BrBG", category: "diverging" },
  { name: "cool", label: "Cool", category: "sequential" },
  { name: "warm", label: "Warm", category: "sequential" },
];

const scaleInterpolators: Record<string, (t: number) => string> = {
  viridis: d3.interpolateViridis,
  plasma: d3.interpolatePlasma,
  inferno: d3.interpolateInferno,
  magma: d3.interpolateMagma,
  cividis: d3.interpolateCividis,
  turbo: d3.interpolateTurbo,
  blues: d3.interpolateBlues,
  greens: d3.interpolateGreens,
  reds: d3.interpolateReds,
  purples: d3.interpolatePurples,
  ylgnbu: d3.interpolateYlGnBu,
  ylorbr: d3.interpolateYlOrBr,
  spectral: d3.interpolateSpectral,
  rdylbu: d3.interpolateRdYlBu,
  rdylgn: d3.interpolateRdYlGn,
  rdbu: d3.interpolateRdBu,
  piyg: d3.interpolatePiYG,
  prgn: d3.interpolatePRGn,
  brbg: d3.interpolateBrBG,
  cool: d3.interpolateCool,
  warm: d3.interpolateWarm,
};

/** Rebuild a continuous legend's colour arrays for a new scale; no DuckDB queries. */
export function recolorContinuousLegend(legend: EmbeddingLegend, scaleName: string): EmbeddingLegend {
  if (!legend.isContinuous || !legend.dataRange) return legend;

  // the WebGL2 shader's colorScheme uniform holds 64; beyond that it greys out
  const NUM_BINS = 64;
  let colors = generatePalette(scaleName, NUM_BINS);
  let { min: minVal, max: maxVal } = legend.dataRange;
  let range = maxVal - minVal;

  // Rebuild legend ticks with new colors
  let tickCount = 5;
  let newLegend: EmbeddingLegend["legend"] = [];
  for (let t = 0; t < tickCount; t++) {
    let binIndex = Math.round((t / (tickCount - 1)) * (NUM_BINS - 1));
    let value = minVal + (binIndex / (NUM_BINS - 1)) * range;
    newLegend.push({
      label: value.toPrecision(3),
      color: colors[binIndex],
      predicate: legend.legend[t]?.predicate ?? null,
      count: 0,
    });
  }

  // Preserve null entry if present
  let nullEntry = legend.legend.find((x) => x.label.includes("null"));
  if (nullEntry) {
    newLegend.push(nullEntry);
  }

  let allColors = [...colors];
  if (nullEntry) allColors.push(nullEntry.color);

  return {
    ...legend,
    legend: newLegend,
    categoryColors: allColors,
    colorScale: scaleName,
  };
}

/** Generate `numBins` colors from a named scale. */
export function generatePalette(scaleName: string, numBins: number = 64): string[] {
  let interpolator = scaleInterpolators[scaleName] ?? d3.interpolateViridis;
  let colors: string[] = [];
  for (let i = 0; i < numBins; i++) {
    colors.push(interpolator(i / (numBins - 1)));
  }
  return colors;
}

// safe to raise; density mode falls back to points above maxDensityModeCategories
const MAX_DISCRETE_CATEGORIES = 48;

/** Deploy-supplied colours per column, keyed by category label. */
let paletteRequest: Promise<Record<string, Record<string, string>>> | null = null;

function deployedPalettes(): Promise<Record<string, Record<string, string>>> {
  paletteRequest ??= fetch("/data/scryo/colors")
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  return paletteRequest;
}

export async function makeCategoryColumn(
  coordinator: Coordinator,
  table: string,
  column: string | null | undefined,
  theme: ChartTheme,
): Promise<EmbeddingLegend | null> {
  if (column == null) {
    return null;
  }
  let [desc] = Array.from(await coordinator.query(SQL.Query.describe(SQL.Query.from(table).select(column))));
  if (desc == null) {
    return null;
  }
  let jsType = jsTypeFromDBType(desc.column_type);
  if (jsType == "string") {
    return await makeDiscreteCategoryColumn(coordinator, table, column, MAX_DISCRETE_CATEGORIES, theme);
  } else if (jsType == "number" || jsType == "Date") {
    let distinct = await distinctCount(coordinator, table, column);
    console.log(`[scryo] Column ${column}: type=${jsType}, distinct=${distinct}`);
    if (distinct <= 10) {
      return await makeDiscreteCategoryColumn(coordinator, table, column, 10, theme);
    } else if (jsType == "number") {
      console.log(`[scryo] Using continuous 256-bin mode for ${column}`);
      try {
        let result = await makeContinuousColumn(coordinator, table, column, theme);
        console.log(`[scryo] Continuous result:`, result.indexColumn, result.categoryColors?.length, "colors");
        return result;
      } catch (e) {
        console.error(`[scryo] makeContinuousColumn failed:`, e);
        return await makeBinnedNumericColumn(coordinator, table, column, theme);
      }
    } else {
      return await makeBinnedNumericColumn(coordinator, table, column, theme);
    }
  }
  return null;
}

async function makeDiscreteCategoryColumn(
  coordinator: Coordinator,
  table: string,
  column: string,
  maxCategories: number,
  theme: ChartTheme,
): Promise<EmbeddingLegend> {
  let indexColumnName = `__ev_${column}_id`;
  let values = Array.from(
    await coordinator.query(
      SQL.Query.from(table)
        .select({ value: SQL.cast(SQL.column(column), "TEXT"), count: SQL.count() })
        .where(SQL.not(SQL.isNull(SQL.cast(SQL.column(column), "TEXT"))))
        .groupby(SQL.cast(SQL.column(column), "TEXT"))
        .orderby(SQL.desc(SQL.count()))
        .limit(maxCategories),
    ),
  ) as { value: string; count: number }[];

  let otherIndex = values.length;
  let nullIndex = values.length + 1;

  // Add the index column.
  await coordinator.exec(`
    ALTER TABLE ${table} ADD COLUMN IF NOT EXISTS ${SQL.column(indexColumnName)} INTEGER DEFAULT 0;
    UPDATE ${table}
    SET ${SQL.column(indexColumnName)} =
      CASE ${SQL.column(column)}::TEXT
      ${values.map(({ value }, i) => SQL.sql`WHEN ${SQL.literal(value)} THEN ${SQL.literal(i)}`).join(" ")}
      ELSE (CASE WHEN ${SQL.column(column)} IS NULL THEN ${SQL.literal(nullIndex)} ELSE ${SQL.literal(otherIndex)} END) END
  `);

  // Count by index.
  let counts = Array.from(
    await coordinator.query(
      SQL.Query.from(table)
        .select({ index: SQL.column(indexColumnName), count: SQL.cast(SQL.count(), "INT") })
        .groupby(SQL.column(indexColumnName)),
    ),
  );
  let countMap = new Map<number, number>();
  for (let item of counts) {
    countMap.set(item.index, item.count);
  }
  let otherCount = countMap.get(otherIndex) ?? 0;
  let nullCount = countMap.get(nullIndex) ?? 0;

  let colors = resolveCategoryColors(theme, values.length);
  let palette = (await deployedPalettes())[column];
  if (palette) {
    colors = values.map(({ value }, i) => palette[value] ?? colors[i]);
  }

  let legend: EmbeddingLegend["legend"] = values.map(({ value }, i) => ({
    label: value,
    color: colors[i],
    predicate: SQL.eq(SQL.cast(SQL.column(column), "TEXT"), SQL.literal(value)),
    count: countMap.get(i) ?? 0,
  }));

  if (otherCount > 0) {
    let { otherCategoryCount } = (
      await coordinator.query(`
        SELECT COUNT(DISTINCT(${SQL.column(column)}::TEXT)) AS otherCategoryCount
        FROM ${table}
        WHERE ${SQL.column(indexColumnName)} = ${SQL.literal(otherIndex)} AND ${SQL.column(column)} IS NOT NULL
      `)
    ).get(0);
    legend.push({
      label: `(other ${otherCategoryCount.toLocaleString()})`,
      color: theme.otherColor,
      predicate:
        values.length > 0
          ? SQL.sql`${SQL.column(column)} IS NOT NULL AND ${SQL.column(column)}::TEXT NOT IN (${values.map((x) => SQL.literal(x.value)).join(",")})`
          : SQL.sql`${SQL.column(column)} IS NOT NULL`,
      count: otherCount,
    });
  }
  if (nullCount > 0) {
    if (otherCount <= 0) {
      // If there is no other, reduce null index by 1 before we add the null item.
      await coordinator.exec(`
          UPDATE ${table}
          SET ${SQL.column(indexColumnName)} = ${SQL.column(indexColumnName)} - 1 WHERE ${SQL.column(indexColumnName)} = ${SQL.literal(nullIndex)}
      `);
      nullIndex -= 1;
    }
    legend.push({
      label: "(null)",
      color: theme.nullColor,
      predicate: SQL.isNull(SQL.column(column)),
      count: nullCount,
    });
  }

  return {
    indexColumn: indexColumnName,
    legend: legend,
  };
}

async function makeBinnedNumericColumn(
  coordinator: Coordinator,
  table: string,
  column: string,
  theme: ChartTheme,
): Promise<EmbeddingLegend> {
  let stats = await computeFieldStats(coordinator, table, SQL.column(column));

  let binning: Binning;
  let expr: SQL.ExprNode;
  let inferFormatter: (v: number[]) => (v: number) => string;

  if (stats?.quantitative) {
    binning = inferBinning(stats.quantitative, { desiredCount: 5 });
    expr = SQL.cast(SQL.column(column), "DOUBLE");
    inferFormatter = inferNumberFormatter;
  } else if (stats?.temporal) {
    binning = inferTimeBinning(stats.temporal, { desiredCount: 5 });
    expr = SQL.epoch_ms(SQL.column(column));
    let hasTimezone = stats.temporal.hasTimezone;
    inferFormatter = (v) => inferTimeFormatter(v, hasTimezone);
  } else {
    throw new Error("invalid data type");
  }

  let indexColumnName = `__ev_${column}_id`;

  let binIndexExpr = binning.binIndexExpr(expr);

  await coordinator.exec(`
    ALTER TABLE ${table} ADD COLUMN IF NOT EXISTS ${SQL.column(indexColumnName)} INTEGER DEFAULT 0;
    UPDATE ${table}
    SET ${SQL.column(indexColumnName)} = ${binIndexExpr}
  `);

  // Count by index.
  let counts = Array.from(
    await coordinator.query(`
      SELECT ${SQL.column(indexColumnName)} AS index, COUNT(*)::INT AS count
      FROM ${table}
      GROUP BY ${SQL.column(indexColumnName)}
      ORDER BY ${SQL.column(indexColumnName)} ASC
    `),
  );

  let minIndex = null;
  let maxIndex = null;
  let index2Count = new Map<number | null, number>();

  for (let { index, count } of counts as { index: number | null; count: number }[]) {
    if (index != null) {
      if (minIndex == null || index < minIndex) {
        minIndex = index;
      }
      if (maxIndex == null || index > maxIndex) {
        maxIndex = index;
      }
    }
    index2Count.set(index, count);
  }

  let legend: EmbeddingLegend["legend"] = [];

  if (minIndex != null && maxIndex != null) {
    let colors = resolveOrdinalColors(theme, maxIndex - minIndex + 1);
    let allValues = new Set<number>();
    for (let index = minIndex; index <= maxIndex; index++) {
      let [lowerBound, upperBound] = binning.rangeForIndex(index);
      allValues.add(lowerBound);
      allValues.add(upperBound);
    }
    let formatter = inferFormatter(Array.from(allValues));
    for (let index = minIndex; index <= maxIndex; index++) {
      let [lowerBound, upperBound] = binning.rangeForIndex(index);
      legend.push({
        label: `[${formatter(lowerBound)}, ${formatter(upperBound)})`,
        color: colors[index - minIndex],
        predicate: SQL.eq(binIndexExpr, SQL.literal(index)),
        count: index2Count.get(index) ?? 0,
      });
    }
  }

  if (index2Count.has(null)) {
    let nullIndex = legend.length;
    await coordinator.exec(`
      UPDATE ${table}
      SET ${SQL.column(indexColumnName)} = ${SQL.literal(nullIndex)}
      WHERE ${SQL.column(indexColumnName)} IS NULL
    `);
    legend.push({
      label: "(null / nan / inf)",
      color: theme.nullColor,
      predicate: SQL.isNull(binIndexExpr),
      count: index2Count.get(null) ?? 0,
    });
  }

  return {
    indexColumn: indexColumnName,
    legend: legend,
  };
}

/**
 * Linear quantization for continuous numeric columns.
 *
 * 64 bins because the shader's colorScheme uniform holds 64 and the category
 * attribute is a signed byte. The null bin sits at 64, past the palette, so it
 * hits the shader's grey fallback.
 */
async function makeContinuousColumn(
  coordinator: Coordinator,
  table: string,
  column: string,
  theme: ChartTheme,
  scaleName: string = "viridis",
): Promise<EmbeddingLegend> {
  const NUM_BINS = 64;
  let indexColumnName = `__ev_${column}_id`;

  let result = Array.from(
    await coordinator.query(`
      SELECT MIN(${SQL.column(column)}) AS min_val, MAX(${SQL.column(column)}) AS max_val
      FROM ${table}
      WHERE ${SQL.column(column)} IS NOT NULL
        AND isfinite(${SQL.column(column)}::DOUBLE)
    `),
  ) as { min_val: number; max_val: number }[];

  let minVal = result[0]?.min_val ?? 0;
  let maxVal = result[0]?.max_val ?? 1;

  // Avoid division by zero
  if (maxVal === minVal) {
    maxVal = minVal + 1;
  }

  let range = maxVal - minVal;
  await coordinator.exec(
    `ALTER TABLE ${table} ADD COLUMN IF NOT EXISTS ${SQL.column(indexColumnName)} INTEGER DEFAULT 0`,
  );
  await coordinator.exec(`
    UPDATE ${table}
    SET ${SQL.column(indexColumnName)} =
      CASE
        WHEN ${SQL.column(column)} IS NULL OR NOT isfinite(${SQL.column(column)}::DOUBLE) THEN ${NUM_BINS}
        ELSE LEAST(${NUM_BINS - 1}, GREATEST(0,
          CAST(FLOOR((${SQL.column(column)}::DOUBLE - ${minVal}) / ${range} * ${NUM_BINS - 1}) AS INTEGER)
        ))
      END
  `);

  let colors = generatePalette(scaleName, NUM_BINS);

  // 5 ticks, not one per bin
  let legend: EmbeddingLegend["legend"] = [];
  let tickCount = 5;
  for (let t = 0; t < tickCount; t++) {
    let binIndex = Math.round((t / (tickCount - 1)) * (NUM_BINS - 1));
    let value = minVal + (binIndex / (NUM_BINS - 1)) * range;
    let label = value.toPrecision(3);
    legend.push({
      label: label,
      color: colors[binIndex],
      predicate: SQL.eq(SQL.column(indexColumnName), SQL.literal(binIndex)),
      count: 0, // not used for continuous legend display
    });
  }

  let nullResult = Array.from(
    await coordinator.query(`
      SELECT COUNT(*)::INT AS count FROM ${table}
      WHERE ${SQL.column(column)} IS NULL OR NOT isfinite(${SQL.column(column)}::DOUBLE)
    `),
  ) as { count: number }[];
  let nullCount = nullResult[0]?.count ?? 0;

  if (nullCount > 0) {
    legend.push({
      label: "(null / nan / inf)",
      color: theme.nullColor,
      predicate: SQL.or(SQL.isNull(SQL.column(column)), SQL.not(SQL.sql`isfinite(${SQL.column(column)}::DOUBLE)`)),
      count: nullCount,
    });
  }

  // null colour lands at index NUM_BINS, past the palette
  let allColors = [...colors];
  if (nullCount > 0) {
    allColors.push(theme.nullColor);
  }

  return {
    indexColumn: indexColumnName,
    legend: legend,
    categoryColors: allColors,
    isContinuous: true,
    colorScale: scaleName,
    dataRange: { min: minVal, max: maxVal },
  };
}

function resolveCategoryColors(theme: ChartTheme, length: number): string[] {
  if (typeof theme.categoryColors == "function") {
    let colors = theme.categoryColors(length);
    if (new Set(colors).size == colors.length) {
      return colors;
    }
    // the category palette cycles past 20; the ordinal ramp stays distinct
    return resolveOrdinalColors(theme, length);
  } else {
    let result: string[] = [];
    for (let i = 0; i < length; i++) {
      result.push(theme.categoryColors[i % theme.categoryColors.length]);
    }
    return result;
  }
}

function resolveOrdinalColors(theme: ChartTheme, length: number): string[] {
  if (typeof theme.ordinalColors == "function") {
    return theme.ordinalColors(length);
  } else {
    if (length == theme.ordinalColors.length) {
      return theme.ordinalColors.slice();
    } else {
      // Re-interpolate
      let interp = d3.interpolateRgbBasis(theme.ordinalColors);
      return Array.from({ length: length }).map((_, i) => interp(i / (length - 1)));
    }
  }
}
