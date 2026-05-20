/* Ganglion docs/tasks graph viewer.
 *
 * Loads viewer/data/manifest.json (produced by tools/doc_graph/extract.py),
 * builds a cytoscape graph with module compound nodes containing atomic-doc
 * children, and wires:
 *   - click a module compound → animated cy.fit() zoom (Prezi-style)
 *   - click an atomic doc      → right drawer with the doc rendered as markdown
 *   - esc / reset              → fit to viewport
 *   - toggles for event vs wikilink edges, label visibility, search
 *
 * No build step; everything is loaded via CDN in index.html.
 */

const MODULE_COLOR = {
  contract: "#5b8def",
  lm: "#6acf6a",
  analyzer: "#f0a04b",
  benchmark: "#b58bff",
  factory: "#ef6f6f",
  other: "#8b96a5",
};

async function loadManifest() {
  const res = await fetch("data/manifest.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`manifest fetch failed: ${res.status}`);
  return await res.json();
}

async function loadDocMarkdown(docId) {
  const res = await fetch(`docs/${docId}.md`, { cache: "no-store" });
  if (!res.ok) return `# ${docId}\n\n_Failed to load source markdown (HTTP ${res.status})._`;
  return await res.text();
}

function buildElements(manifest) {
  const elements = [];
  const moduleById = Object.fromEntries(manifest.modules.map((m) => [m.id, m]));

  // module compound parents
  for (const mod of manifest.modules) {
    elements.push({
      group: "nodes",
      data: {
        id: `mod:${mod.id}`,
        label: mod.label,
        kind: "module",
        module: mod.id,
      },
      classes: `module mod-${mod.id}`,
      selectable: true,
    });
  }

  // doc nodes — children of their module compound
  for (const node of manifest.nodes) {
    const parent = moduleById[node.module] ? `mod:${node.module}` : undefined;
    elements.push({
      group: "nodes",
      data: {
        id: node.id,
        label: node.title || node.id,
        kind: node.kind,
        module: node.module,
        summary: node.summary,
        events_consumed: node.events_consumed,
        events_emitted: node.events_emitted,
        in_scope: node.in_scope,
        out_of_scope: node.out_of_scope,
        wikilinks: node.wikilinks,
        lines: node.lines,
        file: node.file,
        parent,
      },
      classes: `doc kind-${node.kind} mod-${node.module}`,
    });
  }

  // edges
  for (const e of manifest.event_edges) {
    elements.push({
      group: "edges",
      data: {
        id: `event:${e.source}->${e.target}:${e.event}`,
        source: e.source,
        target: e.target,
        label: e.event,
        kind: "event",
      },
      classes: "event-edge",
    });
  }
  for (const e of manifest.wikilink_edges) {
    elements.push({
      group: "edges",
      data: {
        id: `wiki:${e.source}->${e.target}`,
        source: e.source,
        target: e.target,
        kind: "wikilink",
      },
      classes: "wiki-edge",
    });
  }
  return elements;
}

function styleSheet() {
  return [
    // module compound nodes
    {
      selector: "node.module",
      style: {
        "background-color": (ele) => MODULE_COLOR[ele.data("module")] || MODULE_COLOR.other,
        "background-opacity": 0.08,
        "border-color": (ele) => MODULE_COLOR[ele.data("module")] || MODULE_COLOR.other,
        "border-width": 2,
        "border-opacity": 0.7,
        "shape": "round-rectangle",
        "label": "data(label)",
        "color": "#cbd2dc",
        "font-size": 14,
        "font-weight": 600,
        "text-valign": "top",
        "text-halign": "center",
        "text-margin-y": -6,
        "padding": 24,
      },
    },
    // doc nodes
    {
      selector: "node.doc",
      style: {
        "background-color": (ele) => MODULE_COLOR[ele.data("module")] || MODULE_COLOR.other,
        "background-opacity": 0.85,
        "border-color": "#0f1216",
        "border-width": 1,
        "shape": "round-rectangle",
        "label": "data(label)",
        "color": "#0f1216",
        "font-size": 11,
        "font-weight": 600,
        "text-valign": "center",
        "text-halign": "center",
        "text-wrap": "wrap",
        "text-max-width": 130,
        "width": 150,
        "height": 44,
      },
    },
    {
      selector: "node.kind-composite",
      style: {
        "border-color": "#fff",
        "border-width": 2,
        "shape": "round-diamond",
        "width": 170,
        "height": 64,
      },
    },
    // event edges — solid with label
    {
      selector: "edge.event-edge",
      style: {
        "curve-style": "bezier",
        "width": 1.5,
        "line-color": "#3a4452",
        "target-arrow-color": "#3a4452",
        "target-arrow-shape": "triangle",
        "label": "data(label)",
        "font-size": 9,
        "color": "#8b96a5",
        "text-rotation": "autorotate",
        "text-background-color": "#0f1216",
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
      },
    },
    // wikilink edges — dashed, hidden by default
    {
      selector: "edge.wiki-edge",
      style: {
        "curve-style": "bezier",
        "width": 0.8,
        "line-color": "#2a313c",
        "line-style": "dashed",
        "target-arrow-color": "#2a313c",
        "target-arrow-shape": "triangle",
        "opacity": 0.4,
      },
    },
    // emphasis
    {
      selector: "node.faded",
      style: { "opacity": 0.15 },
    },
    {
      selector: "edge.faded",
      style: { "opacity": 0.07 },
    },
    {
      selector: "edge.highlighted",
      style: {
        "line-color": "#58a6ff",
        "target-arrow-color": "#58a6ff",
        "width": 2.5,
        "color": "#e6edf3",
        "z-index": 999,
      },
    },
    {
      selector: "node.highlighted",
      style: {
        "border-color": "#58a6ff",
        "border-width": 3,
        "z-index": 999,
      },
    },
    {
      selector: ".hidden-edge",
      style: { "display": "none" },
    },
    {
      selector: ".hidden-label",
      style: { "label": "" },
    },
  ];
}

function applyFcose(cy) {
  cy.layout({
    name: "fcose",
    animate: false,
    randomize: true,
    nodeRepulsion: 9000,
    idealEdgeLength: 110,
    nodeSeparation: 90,
    gravity: 0.25,
    gravityRangeCompound: 1.4,
    packComponents: true,
    quality: "proof",
  }).run();
}

function setEdgeVisibility(cy, kind, visible) {
  cy.edges(`.${kind}-edge`).forEach((e) => {
    if (visible) e.removeClass("hidden-edge");
    else e.addClass("hidden-edge");
  });
}

function setEventLabelVisibility(cy, visible) {
  cy.edges(".event-edge").forEach((e) => {
    if (visible) e.removeClass("hidden-label");
    else e.addClass("hidden-label");
  });
}

function highlightFocus(cy, node) {
  cy.elements().addClass("faded");
  node.removeClass("faded");
  const neighborhood = node.connectedEdges().union(node.connectedEdges().connectedNodes());
  neighborhood.removeClass("faded");
  node.connectedEdges().addClass("highlighted");
  node.addClass("highlighted");
  // also keep the parent module visible
  if (node.parent().length) {
    node.parent().removeClass("faded");
  }
}

function clearHighlight(cy) {
  cy.elements().removeClass("faded highlighted");
}

function resizeCyAfterDrawer(cy) {
  // cytoscape doesn't auto-detect container resize; nudge it after the CSS transition.
  setTimeout(() => {
    cy.resize();
  }, 240);
}

function fillDrawer(node, mdHtml) {
  const data = node.data();
  document.getElementById("drawer-title").textContent = data.label;
  const kindChip = document.getElementById("drawer-kind");
  kindChip.textContent = data.kind;
  kindChip.className = `chip kind-${data.kind}`;
  const modChip = document.getElementById("drawer-module");
  modChip.textContent = data.module;
  modChip.className = `chip module-${data.module}`;
  document.getElementById("drawer-lines").textContent = `${data.lines} lines`;

  const consumedUl = document.getElementById("drawer-consumed");
  consumedUl.innerHTML = "";
  for (const ev of data.events_consumed || []) {
    const li = document.createElement("li");
    li.textContent = ev;
    consumedUl.appendChild(li);
  }
  if (!consumedUl.children.length) consumedUl.innerHTML = "<li>—</li>";

  const emittedUl = document.getElementById("drawer-emitted");
  emittedUl.innerHTML = "";
  for (const ev of data.events_emitted || []) {
    const li = document.createElement("li");
    li.textContent = ev;
    emittedUl.appendChild(li);
  }
  if (!emittedUl.children.length) emittedUl.innerHTML = "<li>—</li>";

  document.getElementById("drawer-body").innerHTML = mdHtml;
  document.getElementById("drawer").classList.remove("hidden");
  document.getElementById("cy").classList.add("with-drawer");
}

function closeDrawer() {
  document.getElementById("drawer").classList.add("hidden");
  document.getElementById("cy").classList.remove("with-drawer");
}

async function openDocDrawer(node) {
  const md = await loadDocMarkdown(node.id());
  // strip the breadcrumb anchor lines at the very top (they look noisy in the drawer)
  const cleaned = md.replace(/^\[← .*?\)\s*$/gm, "").trimStart();
  const html = marked.parse(cleaned, { mangle: false, headerIds: false });
  fillDrawer(node, html);
}

function zoomToModule(cy, moduleNode) {
  cy.animate(
    { fit: { eles: moduleNode, padding: 60 } },
    { duration: 450, easing: "ease-in-out-cubic" },
  );
}

function fitAll(cy) {
  cy.animate(
    { fit: { eles: cy.elements(), padding: 40 } },
    { duration: 380, easing: "ease-in-out-cubic" },
  );
}

async function main() {
  const status = document.getElementById("status");
  let manifest;
  try {
    manifest = await loadManifest();
  } catch (err) {
    status.textContent = `failed to load manifest: ${err.message}`;
    return;
  }

  const elements = buildElements(manifest);
  const cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    style: styleSheet(),
    wheelSensitivity: 0.25,
    minZoom: 0.15,
    maxZoom: 3.0,
  });

  applyFcose(cy);
  // wikilink edges hidden initially (per user choice — only event labels by default)
  setEdgeVisibility(cy, "wiki", false);
  setEventLabelVisibility(cy, true);

  const counts = {
    modules: manifest.modules.length,
    docs: manifest.nodes.length,
    eventEdges: manifest.event_edges.length,
    wikilinkEdges: manifest.wikilink_edges.length,
  };
  status.textContent = `${counts.modules} modules · ${counts.docs} task docs · ${counts.eventEdges} event edges · ${counts.wikilinkEdges} wikilink edges`;
  if ((manifest.dangling_events || []).length) {
    const dangling = manifest.dangling_events
      .map((d) => d.event)
      .slice(0, 6)
      .join(", ");
    const more = manifest.dangling_events.length > 6 ? ` (+${manifest.dangling_events.length - 6} more)` : "";
    document.getElementById("dangling-warn").textContent = `· dangling: ${dangling}${more}`;
  }

  cy.on("tap", "node.doc", async (ev) => {
    const node = ev.target;
    highlightFocus(cy, node);
    await openDocDrawer(node);
    resizeCyAfterDrawer(cy);
  });

  cy.on("tap", "node.module", (ev) => {
    const node = ev.target;
    clearHighlight(cy);
    closeDrawer();
    zoomToModule(cy, node);
  });

  cy.on("tap", (ev) => {
    if (ev.target === cy) {
      // background click
      clearHighlight(cy);
      closeDrawer();
    }
  });

  document.getElementById("drawer-close").addEventListener("click", () => {
    clearHighlight(cy);
    closeDrawer();
    resizeCyAfterDrawer(cy);
  });

  document.getElementById("reset").addEventListener("click", () => {
    clearHighlight(cy);
    closeDrawer();
    fitAll(cy);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      clearHighlight(cy);
      closeDrawer();
      resizeCyAfterDrawer(cy);
      fitAll(cy);
    }
  });

  document.getElementById("toggle-event").addEventListener("change", (e) => {
    setEdgeVisibility(cy, "event", e.target.checked);
  });
  document.getElementById("toggle-wikilink").addEventListener("change", (e) => {
    setEdgeVisibility(cy, "wiki", e.target.checked);
  });
  document.getElementById("toggle-event-labels").addEventListener("change", (e) => {
    setEventLabelVisibility(cy, e.target.checked);
  });

  const searchInput = document.getElementById("search");
  searchInput.addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) {
      clearHighlight(cy);
      return;
    }
    const hits = cy.nodes("node.doc").filter((n) => n.id().toLowerCase().includes(q));
    if (!hits.length) {
      clearHighlight(cy);
      return;
    }
    cy.elements().addClass("faded");
    hits.removeClass("faded").addClass("highlighted");
    hits.connectedEdges().removeClass("faded");
    hits.connectedEdges().connectedNodes().removeClass("faded");
    if (hits.length === 1) {
      cy.animate({ fit: { eles: hits[0], padding: 120 } }, { duration: 320 });
    }
  });

  // initial fit after layout settles
  setTimeout(() => fitAll(cy), 50);
}

main().catch((err) => {
  console.error(err);
  document.getElementById("status").textContent = "init error — see console";
});
