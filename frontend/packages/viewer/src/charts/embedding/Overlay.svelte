<!-- Copyright (c) 2025 Apple Inc. Licensed under MIT License. -->
<script lang="ts">
  import type { DataPoint, OverlayProxy } from "@embedding-atlas/component";

  interface ClusterLabel {
    x: number;
    y: number;
    text: string;
    priority?: number;
  }

  interface Props {
    center?: DataPoint | null;
    points?: DataPoint[];
    labels?: ClusterLabel[];
    proxy: OverlayProxy;
  }

  let { center, points, labels, proxy }: Props = $props();

  const FONT_SIZE = 12;
  // estimated; SVG text can't be measured before layout
  const CHAR_WIDTH = 0.58 * FONT_SIZE;
  const PADDING = 4;

  // sorted separately so a pan/zoom frame only redoes the placement pass
  let ordered = $derived([...(labels ?? [])].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0)));

  // dropped labels are intentional; they reappear as zoom separates their centres
  let placed = $derived.by(() => {
    let boxes: { x0: number; x1: number; y0: number; y1: number }[] = [];
    let result: { text: string; x: number; y: number }[] = [];
    for (let label of ordered) {
      let loc = proxy.location(label.x, label.y);
      if (!(loc.x >= 0 && loc.x <= proxy.width && loc.y >= 0 && loc.y <= proxy.height)) {
        continue;
      }
      let halfWidth = (label.text.length * CHAR_WIDTH) / 2 + PADDING;
      let halfHeight = FONT_SIZE / 2 + PADDING;
      let box = {
        x0: loc.x - halfWidth,
        x1: loc.x + halfWidth,
        y0: loc.y - halfHeight,
        y1: loc.y + halfHeight,
      };
      if (boxes.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) {
        continue;
      }
      boxes.push(box);
      result.push({ text: label.text, x: loc.x, y: loc.y });
    }
    return result;
  });
</script>

<svg width={proxy.width} height={proxy.height}>
  <g>
    {#if center != null}
      {@const l1 = proxy.location(center.x, center.y)}
      {#each points ?? [] as point}
        {@const l2 = proxy.location(point.x, point.y)}
        <line x1={l1.x} y1={l1.y} x2={l2.x} y2={l2.y} class="stroke-orange-500" />
      {/each}
    {/if}
    {#each points ?? [] as point}
      {@const loc = proxy.location(point.x, point.y)}
      <circle cx={loc.x} cy={loc.y} r={4} class="fill-orange-500 stroke-orange-700 stroke-2" />
    {/each}
  </g>
  <g>
    {#each placed as label}
      <text
        x={label.x}
        y={label.y}
        text-anchor="middle"
        dominant-baseline="central"
        class="select-none fill-slate-900 dark:fill-white"
        style="font-size: {FONT_SIZE}px; font-weight: 600;">{label.text}</text
      >
    {/each}
  </g>
</svg>
