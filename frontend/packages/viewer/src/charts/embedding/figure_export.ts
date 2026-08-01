// Export a figure region (WebGL canvases + SVG overlays) to PNG or SVG.
// The point cloud only exists as a canvas, so the SVG export embeds it as a
// raster image; overlay labels stay vectors.

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    let img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

interface Placed {
  el: Element;
  x: number;
  y: number;
  w: number;
  h: number;
}

function placedChildren(root: HTMLElement, selector: string): Placed[] {
  let base = root.getBoundingClientRect();
  return Array.from(root.querySelectorAll(selector))
    .map((el) => {
      let r = el.getBoundingClientRect();
      return { el, x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height };
    })
    .filter((p) => p.w > 0 && p.h > 0);
}

export function escapeXml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

// Rasterize a standalone SVG string (its root width/height set the size).
export async function svgStringToPngBlob(svg: string, scale = 2, background = "#ffffff"): Promise<Blob> {
  let m = svg.match(/width="(\d+(?:\.\d+)?)"\s+height="(\d+(?:\.\d+)?)"/);
  let W = m ? Math.round(+m[1]) : 800;
  let H = m ? Math.round(+m[2]) : 600;
  let img = await loadImage("data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg));
  let out = document.createElement("canvas");
  out.width = W * scale;
  out.height = H * scale;
  let ctx = out.getContext("2d")!;
  ctx.scale(scale, scale);
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, W, H);
  ctx.drawImage(img, 0, 0, W, H);
  return await new Promise((resolve) => out.toBlob((b) => resolve(b!), "image/png"));
}

// Rasterize the point canvases plus the cluster-label overlay, which is selected
// by class so the built-in control icons and scalebar stay out of the figure.
async function rasterizeRoot(root: HTMLElement, scale: number): Promise<HTMLCanvasElement> {
  let base = root.getBoundingClientRect();
  let W = Math.round(base.width);
  let H = Math.round(base.height);
  let out = document.createElement("canvas");
  out.width = W * scale;
  out.height = H * scale;
  let ctx = out.getContext("2d")!;
  ctx.scale(scale, scale);
  for (let p of placedChildren(root, "canvas")) {
    ctx.drawImage(await loadImage((p.el as HTMLCanvasElement).toDataURL("image/png")), p.x, p.y, p.w, p.h);
  }
  for (let p of placedChildren(root, "svg.scryo-figure-overlay")) {
    let markup = new XMLSerializer().serializeToString(p.el);
    ctx.drawImage(await loadImage("data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup)), p.x, p.y, p.w, p.h);
  }
  return out;
}

export interface LegendSpec {
  title: string;
  items?: { label: string; color: string }[]; // categorical swatches
  gradient?: { colors: string[]; min: string; max: string }; // continuous ramp
}

// Crop the point cloud to its content, pad to a square, and add a legend.
export async function composeEmbeddingFigure(
  root: HTMLElement,
  format: "png" | "svg",
  filename: string,
  legend: LegendSpec | null,
  scale = 3,
): Promise<void> {
  let raster = await rasterizeRoot(root, scale);
  let cw = raster.width;
  let ch = raster.height;

  // bbox on a downscaled copy; getImageData on the full 3x raster is ~35 MB,
  // and the 3% pad below absorbs the precision loss
  let sw = 400;
  let sh = Math.max(1, Math.round((ch / cw) * sw));
  let small = document.createElement("canvas");
  small.width = sw;
  small.height = sh;
  let sctx = small.getContext("2d")!;
  sctx.drawImage(raster, 0, 0, sw, sh);
  let data = sctx.getImageData(0, 0, sw, sh).data;
  let minX = sw;
  let minY = sh;
  let maxX = 0;
  let maxY = 0;
  for (let y = 0; y < sh; y++) {
    for (let x = 0; x < sw; x++) {
      let i = (y * sw + x) * 4;
      if (data[i] < 244 || data[i + 1] < 244 || data[i + 2] < 244) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX <= minX || maxY <= minY) {
    minX = 0;
    minY = 0;
    maxX = cw;
    maxY = ch;
  } else {
    minX = Math.round((minX / sw) * cw);
    maxX = Math.round((maxX / sw) * cw);
    minY = Math.round((minY / sh) * ch);
    maxY = Math.round((maxY / sh) * ch);
  }
  let pad = Math.round(Math.min(cw, ch) * 0.03);
  minX = Math.max(0, minX - pad);
  minY = Math.max(0, minY - pad);
  maxX = Math.min(cw, maxX + pad);
  maxY = Math.min(ch, maxY + pad);
  let bw = maxX - minX;
  let bh = maxY - minY;
  let side = Math.max(bw, bh); // square plot area

  // right-hand legend column, sized to the longest label so nothing truncates
  let font = 13 * scale;
  let legendW = 0;
  if (legend) {
    let labelLens = legend.items
      ? legend.items.map((i) => i.label.length)
      : legend.gradient
        ? [legend.gradient.min.length, legend.gradient.max.length]
        : [];
    let longest = Math.max(legend.title.length, ...labelLens);
    legendW = Math.round((28 + longest * 7.2) * scale);
  }

  let out = document.createElement("canvas");
  out.width = side + legendW;
  out.height = side;
  let octx = out.getContext("2d")!;
  octx.fillStyle = "#ffffff";
  octx.fillRect(0, 0, out.width, out.height);
  // center the (possibly non-square) content in the square plot area
  octx.drawImage(raster, minX, minY, bw, bh, (side - bw) / 2, (side - bh) / 2, bw, bh);

  if (legend) {
    let lx = side + 12 * scale;
    let ly = Math.round(side * 0.08);
    octx.fillStyle = "#000000";
    octx.font = `bold ${font}px Arial, sans-serif`;
    octx.textBaseline = "middle";
    octx.fillText(legend.title, lx, ly);
    ly += Math.round(20 * scale);
    octx.font = `${font}px Arial, sans-serif`;
    if (legend.items) {
      let sw = Math.round(13 * scale);
      let row = Math.round(20 * scale);
      for (let it of legend.items) {
        octx.fillStyle = it.color;
        octx.fillRect(lx, ly - sw / 2, sw, sw);
        octx.fillStyle = "#000000";
        octx.fillText(it.label, lx + sw + 6 * scale, ly);
        ly += row;
      }
    } else if (legend.gradient) {
      let gw = Math.round(16 * scale);
      let gh = Math.round(side * 0.5);
      let grad = octx.createLinearGradient(0, ly, 0, ly + gh);
      legend.gradient.colors.forEach((c, i) => grad.addColorStop(i / (legend.gradient!.colors.length - 1), c));
      octx.fillStyle = grad;
      octx.fillRect(lx, ly, gw, gh);
      octx.fillStyle = "#000000";
      octx.fillText(legend.gradient.max, lx + gw + 6 * scale, ly);
      octx.fillText(legend.gradient.min, lx + gw + 6 * scale, ly + gh);
    }
  }

  if (format === "png") {
    downloadBlob(await new Promise<Blob>((r) => out.toBlob((b) => r(b!), "image/png")), `${filename}.png`, "image/png");
  } else {
    // SVG: the composed raster as one <image> (points are inherently raster)
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${out.width}" height="${out.height}"><image width="${out.width}" height="${out.height}" href="${out.toDataURL("image/png")}"/></svg>`;
    downloadBlob(svg, `${filename}.svg`, "image/svg+xml");
  }
}

export function downloadBlob(data: Blob | string, filename: string, mime = "image/svg+xml"): void {
  let blob = typeof data === "string" ? new Blob([data], { type: mime }) : data;
  let url = URL.createObjectURL(blob);
  let a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
