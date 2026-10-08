/* ============================================================
   GANGLION CLI REFERENCE  ·  renderer
   Reads assets/cli.json (generated — tools/cli_docs/build.py) and
   paints the rail plus one pane per command or guide.

   Hand-written here: the four prose guides and the layout.
   Everything else — commands, flags, choices, routes, captured
   output, the coverage matrix — comes from the JSON, so a new
   command appears without touching this file.

   Vanilla ES2018. No build step, no external libraries. Every
   value that reaches innerHTML goes through esc() first: captured
   stdout contains model output and Korean prompts.
   ============================================================ */
(function () {
  'use strict';

  var SPEC = null;
  var LEAF_BY_ID = {};

  // ---- helpers -----------------------------------------------
  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function tag(text, variant) {
    return '<span class="tag' + (variant ? ' tag--' + variant : '') + '">' + esc(text) + '</span>';
  }

  var KIND_VARIANT = { 'P': 'in', 'W': 'warn', 'W†': 'event', 'local': 'ghost' };
  var KIND_TITLE = {
    'P': 'projection — reads run-bundle files, creates none',
    'W': 'write — calls exactly one primitive through the console writer',
    'W†': 'write — a GET that persists one idempotent artifact',
    'local': 'local — touches no route'
  };
  var STATUS_VARIANT = { shipped: 'in', stub: 'fail', unexercised: 'ghost' };

  function kindTag(kind) {
    return '<span class="tag tag--' + (KIND_VARIANT[kind] || 'ghost') + '" title="' +
      esc(KIND_TITLE[kind] || kind) + '">' + esc(kind) + '</span>';
  }

  /** A <table class="dtable"> from headers + row arrays of ready HTML. */
  function table(headers, rows, empty) {
    if (!rows.length) return '<p class="cli__empty">' + esc(empty || '(none)') + '</p>';
    var head = headers.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('');
    var body = rows.map(function (cells) {
      return '<tr>' + cells.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>';
    }).join('');
    return '<table class="dtable"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>';
  }

  function panel(title, tools, bodyHtml, flush) {
    return '<section class="panel">' +
      '<div class="panel__head"><div class="panel__head-title">' + esc(title) + '</div>' +
      '<div class="panel__head-tools">' + (tools || '') + '</div></div>' +
      '<div class="panel__body' + (flush ? ' panel__body--flush' : '') + '">' + bodyHtml + '</div></section>';
  }

  function chips(items) {
    if (!items.length) return '<span class="cli__empty">(none)</span>';
    return '<div class="chips">' + items.join('') + '</div>';
  }

  function mono(text) { return '<span class="t-eng-sm">' + esc(text) + '</span>'; }

  // ---- generated fragments -----------------------------------
  function flagRows(flags) {
    return flags.map(function (flag) {
      var names = flag.options.join(', ') + (flag.kind === 'value' && flag.metavar ? ' ' + flag.metavar : '');
      var choices = flag.choices ? chips(flag.choices.map(function (c) { return tag(c, 'ghost'); })) : '<span class="cli__empty">—</span>';
      var dflt = (flag.default === null || flag.default === undefined || flag.default === false)
        ? '<span class="cli__empty">—</span>' : mono(String(flag.default));
      return [
        '<b class="t-eng-sm">' + esc(names) + '</b>' + (flag.required ? ' ' + tag('required', 'warn') : ''),
        esc(flag.help),
        choices,
        dflt
      ];
    });
  }

  function positionalRows(positionals) {
    return positionals.map(function (pos) {
      var optional = pos.nargs === '?' || pos.nargs === '*';
      return [
        '<b class="t-eng-sm">' + esc(pos.metavar || pos.dest) + '</b>',
        optional ? tag('optional', 'ghost') : tag('required', 'warn'),
        esc(pos.help)
      ];
    });
  }

  function exampleBlock(ex) {
    var out = '';
    if (ex.note) out += '<p class="cli__note">' + esc(ex.note) + '</p>';
    out += '<div class="cli__cmd">' + esc(ex.argv.join(' ')) + '</div>';
    if (ex.stdout) out += '<pre class="code cli__out">' + esc(ex.stdout) + '</pre>';
    if (ex.stderr) out += '<pre class="code cli__out cli__err">' + esc(ex.stderr) + '</pre>';
    if (!ex.stdout && !ex.stderr) out += '<pre class="code cli__out">(no output)</pre>';
    out += '<div class="cli__exit">exit ' + esc(ex.exit_code) +
      (ex.exit_code === 0 ? '' : ' — the route’s own error, surfaced unchanged') + '</div>';
    return '<div class="stack-12">' + out + '</div>';
  }

  // ---- panes: one command ------------------------------------
  function renderLeaf(leaf) {
    var own = leaf.flags.filter(function (f) { return !f.common && f.kind !== 'version'; });
    var routes = (leaf.routes || []).map(function (r) {
      return tag(r.method + ' ' + r.path, r.method === 'GET' ? 'in' : 'warn');
    });

    var head = '<section class="page__head"><div>' +
      '<div class="page__id">' + esc(leaf.noun) + (leaf.verb ? ' <span>◦</span> ' + esc(leaf.verb) : '') + '</div>' +
      '<div class="cli__sig">' + esc(leaf.command) + '</div>' +
      '<p class="page__sub">' + esc(leaf.description || leaf.help) + '</p>' +
      '</div><div class="folio">' + kindTag(leaf.kind) + ' ' +
      tag(leaf.status, STATUS_VARIANT[leaf.status] || 'ghost') + '<br><br>' +
      'Route<br>' + (routes.length ? routes.join(' ') : '<span class="cli__empty">no route</span>') +
      '</div></section>';

    var body = '';
    body += panel('synopsis', '', '<pre class="code">' + esc(leaf.usage) + '</pre>');
    if (leaf.positionals.length) {
      body += panel('arguments', '', table(['argument', '', 'what it means'], positionalRows(leaf.positionals)), true);
    }
    body += panel(
      own.length ? 'flags' : 'flags',
      '<a class="cli__link" style="padding:0" href="#doc-addressing">+ ' +
        esc(SPEC.common_flags.length) + ' common flags →</a>',
      table(['flag', 'what it means', 'choices', 'default'], flagRows(own),
            'no flags of its own — only the common ones'),
      true
    );
    if (leaf.exclusive_groups.length) {
      body += panel('mutually exclusive', '', chips(leaf.exclusive_groups.map(function (g) {
        return tag(g.join(' | '), 'lock');
      })));
    }
    body += panel(
      'measured output',
      '<span class="t-mute fs-10">captured by running the command · ids and clocks normalised</span>',
      leaf.examples.length
        ? leaf.examples.map(exampleBlock).join('<div style="height:20px"></div>')
        : '<p class="cli__empty">no example declared in tools/cli_docs/examples.py</p>'
    );
    return head + body;
  }

  // ---- panes: the hand-written guides ------------------------
  function guideStart() {
    return '<section class="page__head"><div>' +
      '<div class="page__id">guide <span>◦</span> five minutes</div>' +
      '<h1 class="page__title">getting <em>started</em></h1>' +
      '<p class="page__sub">Everything below runs offline against the <b>rules</b> stand-in model — no ' +
      '<span class="t-amber">DASHSCOPE_API_KEY</span>, no GPU, no network.</p>' +
      '</div></section>' +
      panel('1 · install', '', '<pre class="code">pip install -e ".[dev]"</pre>' +
        '<p class="cli__note">That puts <b>ganglion</b> on PATH. Without the install (or without the script ' +
        'directory on PATH) every command below also works as ' +
        '<span class="t-eng-sm">python -m ganglion.ctl …</span>, which is the form the rest of the ' +
        'repository documents for <span class="t-eng-sm">ganglion.cli</span> and ' +
        '<span class="t-eng-sm">ganglion.console</span> too.</p>') +
      panel('2 · make something to look at', '',
        '<pre class="code">python -m ganglion.console seed --runs runs/scratch --limit 30</pre>' +
        '<p class="cli__note">Writes two run bundles — <b>rules-seed</b> and a deliberately degraded ' +
        '<b>rules-degraded-seed</b> — and analyses both, so the failure taxonomy, the proposed patches and a ' +
        'run comparison all have real rows. Takes about a fifth of a second. Point it at a scratch directory: ' +
        '<span class="t-eng-sm">runs/traces</span> is checked-in data, not a workspace.</p>') +
      panel('3 · point the CLI at it, once', '',
        '<pre class="code">ganglion use --runs runs/scratch --catalog iot_light_5 --model rules</pre>' +
        '<p class="cli__note">Saved to <span class="t-eng-sm">.ganglion/context.json</span> (gitignored). ' +
        'Every later command omits those flags — see <a class="t-teal" href="#doc-addressing">addressing</a>.</p>') +
      panel('4 · the loop the CLI exists for', '',
        '<pre class="code">' + esc([
          'ganglion run list                              # what has been run',
          'ganglion ask 거실 불 켜줘                        # one inference, F⁰ vs Fᴷ',
          'ganglion trace list rules-degraded-seed --filter wrong',
          'ganglion label add <trace_id> --run rules-degraded-seed \\',
          '        --verdict incorrect --expected gold.json   # a human verdict beats dataset gold',
          'ganglion run analyze rules-degraded-seed       # classify → attribute → synthesise',
          'ganglion patch list rules-degraded-seed        # what the analyzer proposes',
          'ganglion patch decide <patch_id> --stage blind --decision accept',
          'ganglion compare rules-seed rules-degraded-seed'
        ].join('\n')) + '</pre>' +
        '<p class="cli__note">Each step leaves a file and an event row in the run bundle. Accepting a patch ' +
        'changes no catalog — it records a judgement; porting it is a reviewed code edit recorded with its ' +
        'commit. Pick any command in the rail for its measured output.</p>');
  }

  function guideAddressing() {
    var envRows = SPEC.environment.map(function (row) {
      return ['<b class="t-eng-sm">' + esc(row.name) + '</b>', esc(row.what), mono(row.read_by)];
    });
    return '<section class="page__head"><div>' +
      '<div class="page__id">guide <span>◦</span> how a command finds its target</div>' +
      '<h1 class="page__title">addressing &amp; <em>context</em></h1>' +
      '<p class="page__sub">A catalog id, a model id, a run id and a session id address almost every command. ' +
      'Passing all four every time would be unusable, so they resolve from a saved context — the terminal ' +
      'equivalent of what the console pages keep in <span class="t-amber">localStorage</span>.</p>' +
      '</div></section>' +
      panel('precedence', '<span class="t-mute fs-10">per field, highest first</span>',
        '<pre class="code">' + esc([
          'flag                 --catalog iot_light_5        one-off, changes nothing on disk',
          'environment          GANGLION_CATALOG=…           per shell',
          'context file         .ganglion/context.json       written by `ganglion use`',
          'default              runs_dir → runs/traces       nothing else has one'
        ].join('\n')) + '</pre>' +
        '<p class="cli__note">A field that resolves to nothing is an error before any route call: exit 1 with ' +
        '<span class="t-eng-sm">missing_context</span>, naming both the flag and the ' +
        '<span class="t-eng-sm">ganglion use</span> form. Nothing is ever guessed.</p>') +
      panel('the context file', '',
        '<pre class="code">ganglion use --catalog iot_light_5 --model rules   # write\n' +
        'ganglion use --show                               # resolve, write nothing\n' +
        'ganglion use --clear                              # empty the four id fields</pre>' +
        '<p class="cli__note">It holds <span class="t-eng-sm">catalog</span>, ' +
        '<span class="t-eng-sm">model</span>, <span class="t-eng-sm">session</span>, ' +
        '<span class="t-eng-sm">run</span> and <span class="t-eng-sm">runs_dir</span>, and is written only by ' +
        '<span class="t-eng-sm">use</span>, <span class="t-eng-sm">session new</span> and ' +
        '<span class="t-eng-sm">ask</span>’s implicit session. A corrupt file is a cache miss, never a ' +
        'failed command. <span class="t-eng-sm">--clear</span> keeps ' +
        '<span class="t-eng-sm">runs_dir</span>: it addresses the store, not a selection.</p>') +
      panel('environment', '', table(['variable', 'what it sets', 'read by'], envRows), true) +
      panel('common flags', '<span class="t-mute fs-10">on every leaf, never on a bare noun</span>',
        table(['flag', 'what it means', 'choices', 'default'], flagRows(SPEC.common_flags)), true);
  }

  function guideOutput() {
    var exitRows = SPEC.exit_codes.map(function (row) {
      return ['<b class="t-eng-sm">' + esc(row.code) + '</b>', esc(row.meaning)];
    });
    return '<section class="page__head"><div>' +
      '<div class="page__id">guide <span>◦</span> what comes back</div>' +
      '<h1 class="page__title">output &amp; <em>exit codes</em></h1>' +
      '<p class="page__sub">Human output is for reading and may change. ' +
      '<span class="t-eng-sm">--json</span> is the contract: the route payload, verbatim.</p>' +
      '</div></section>' +
      panel('three modes', '',
        table(['mode', 'what it prints', 'stability'], [
          [mono('(default)'), 'a terminal rendering: tables, key/value blocks, bar charts, plan call lists',
           'may change between versions'],
          [mono('--json'), 'the <b>/api/*</b> payload with nothing added, removed, renamed or reordered',
           tag('stable', 'in') + ' bind scripts to this'],
          [mono('--jsonl'), 'one JSON object per row of the payload’s primary list', 'follows --json']
        ]) +
        '<p class="cli__note">Human-only selectors — <span class="t-eng-sm">--part</span>, ' +
        '<span class="t-eng-sm">--tail</span>, <span class="t-eng-sm">--name</span>, ' +
        '<span class="t-eng-sm">--per-case</span>, <span class="t-eng-sm">--raw</span> — shape the reading ' +
        'view only. Under <span class="t-eng-sm">--json</span> the full payload is printed and the selector ' +
        'is ignored, so a script cannot accidentally depend on a display choice.</p>', true) +
      panel('exit codes', '', table(['code', 'meaning'], exitRows), true) +
      panel('errors', '',
        '<pre class="code">' + esc([
          '$ ganglion catalog show bfcl/simple_python',
          'error: unknown_catalog — bfcl/simple_python names per-case catalogs; not resolvable'
        ].join('\n')) + '</pre>' +
        '<p class="cli__note">One line on stderr, nothing on stdout, and the route’s own ' +
        '<b>error</b> slug is never reworded — so the CLI and the browser report the same failure by the same ' +
        'name. A problem the CLI can see for itself (an unreadable file, a missing context field) is raised ' +
        '<i>before</i> the route is called, as <span class="t-eng-sm">bad_input</span> or ' +
        '<span class="t-eng-sm">missing_context</span>.</p>');
  }

  function guideCoverage() {
    var cov = SPEC.coverage;
    var rows = cov.routes.map(function (row) {
      var reached = row.reached_by.map(function (id) {
        var leaf = LEAF_BY_ID[id];
        return '<a class="t-teal" href="#cmd-' + esc(id) + '">' + esc(leaf ? leaf.command : id) + '</a>';
      });
      return [
        kindTag(row.kind),
        '<b class="t-eng-sm">' + esc(row.method) + '</b> ' + mono(row.path),
        reached.length ? reached.join('<br>') : tag('browser only', 'fail')
      ];
    });
    return '<section class="page__head"><div>' +
      '<div class="page__id">guide <span>◦</span> cli vs browser</div>' +
      '<h1 class="page__title">route <em>coverage</em></h1>' +
      '<p class="page__sub">Every route the operator console declares, and the command that reaches it. ' +
      'A row with no command is a capability you still need a browser for — which makes this table the ' +
      'roadmap as much as the reference.</p>' +
      '</div></section>' +
      panel('console routes', '<span class="t-mute fs-10">' + esc(cov.n_reached) + ' of ' +
        esc(cov.n_routes) + ' reachable from the terminal</span>',
        table(['kind', 'route', 'reached by'], rows), true) +
      (cov.unmatched_cli_routes.length
        ? panel('unmatched', '', chips(cov.unmatched_cli_routes.map(function (r) { return tag(r, 'fail'); })))
        : '');
  }

  var GUIDES = [
    { id: 'doc-start', label: 'getting started', render: guideStart },
    { id: 'doc-addressing', label: 'addressing & context', render: guideAddressing },
    { id: 'doc-output', label: 'output & exit codes', render: guideOutput },
    { id: 'doc-coverage', label: 'route coverage', render: guideCoverage }
  ];

  // ---- rail + routing ----------------------------------------
  function buildRail() {
    var html = '<div class="cli__group"><div class="cli__grouph">guides</div>';
    GUIDES.forEach(function (guide) {
      html += '<a class="cli__link" data-id="' + guide.id + '" data-find="' + esc(guide.label) +
        '" href="#' + guide.id + '">' + esc(guide.label) + '</a>';
    });
    html += '</div>';
    SPEC.nouns.forEach(function (noun) {
      if (!noun.leaves.length) return;
      html += '<div class="cli__group"><div class="cli__grouph">' + esc(noun.name) + '</div>';
      noun.leaves.forEach(function (id) {
        var leaf = LEAF_BY_ID[id];
        html += '<a class="cli__link" data-id="cmd-' + esc(id) + '" data-find="' +
          esc(leaf.command + ' ' + leaf.help) + '" href="#cmd-' + esc(id) + '">' +
          esc(leaf.verb || leaf.noun) +
          '<span class="cli__link-k">' + esc(leaf.kind) + '</span></a>';
      });
      html += '</div>';
    });
    document.getElementById('rail').innerHTML = html;
  }

  function show(hash) {
    var id = (hash || '').replace(/^#/, '') || 'doc-start';
    var pane = document.getElementById('pane');
    var guide = GUIDES.filter(function (g) { return g.id === id; })[0];
    if (guide) {
      pane.innerHTML = guide.render();
    } else if (id.indexOf('cmd-') === 0 && LEAF_BY_ID[id.slice(4)]) {
      pane.innerHTML = renderLeaf(LEAF_BY_ID[id.slice(4)]);
    } else {
      pane.innerHTML = '<p class="cli__empty">no such section: ' + esc(id) + '</p>';
      id = '';
    }
    Array.prototype.forEach.call(document.querySelectorAll('.cli__link'), function (link) {
      if (link.dataset.id) link.classList.toggle('active', link.dataset.id === id);
    });
    window.scrollTo(0, 0);
  }

  function wireFilter() {
    document.getElementById('find').addEventListener('input', function (event) {
      var needle = event.target.value.trim().toLowerCase();
      Array.prototype.forEach.call(document.querySelectorAll('#rail .cli__link'), function (link) {
        var hit = !needle || (link.dataset.find || '').toLowerCase().indexOf(needle) >= 0;
        link.classList.toggle('cli__hide', !hit);
      });
      Array.prototype.forEach.call(document.querySelectorAll('#rail .cli__group'), function (group) {
        var visible = group.querySelectorAll('.cli__link:not(.cli__hide)').length;
        group.classList.toggle('cli__hide', !visible);
      });
    });
  }

  function paintStamp() {
    var meta = SPEC._generated;
    var shipped = SPEC.leaves.filter(function (l) { return l.status === 'shipped'; }).length;
    document.getElementById('head-count').textContent =
      meta.n_leaves + ' commands · ' + meta.n_examples + ' measured examples';
    document.getElementById('stamp').innerHTML = [
      ['Commands', meta.n_leaves],
      ['Shipped', shipped + ' / ' + meta.n_leaves],
      ['Routes', SPEC.coverage.n_reached + ' / ' + SPEC.coverage.n_routes],
      ['Version', meta.ganglion_version]
    ].map(function (cell) {
      return '<div class="opbar__meta-cell"><div class="opbar__meta-k">' + esc(cell[0]) +
        '</div><div class="opbar__meta-v">' + esc(cell[1]) + '</div></div>';
    }).join('');
    document.getElementById('folio').innerHTML =
      'Generated by <b>tools/cli_docs/build.py</b><br>' +
      'ganglion <b>' + esc(meta.ganglion_version) + '</b>' +
      (meta.commit ? ' · commit <b>' + esc(meta.commit) + '</b>' : '') + '<br>' +
      '<b>' + esc(meta.n_leaves) + '</b> commands · <b>' + esc(meta.n_examples) + '</b> examples<br>' +
      '<b>' + esc(SPEC.coverage.n_reached) + ' / ' + esc(SPEC.coverage.n_routes) + '</b> console routes reached';
  }

  // ---- boot --------------------------------------------------
  function boot(spec) {
    SPEC = spec;
    spec.leaves.forEach(function (leaf) { LEAF_BY_ID[leaf.id] = leaf; });
    paintStamp();
    buildRail();
    wireFilter();
    window.addEventListener('hashchange', function () { show(location.hash); });
    show(location.hash);
  }

  function fail(message) {
    document.getElementById('pane').innerHTML =
      '<div class="callout">Could not load <b>assets/cli.json</b> (' + esc(message) + ').<br><br>' +
      'Generate it with <b>python tools/cli_docs/build.py</b>, then serve this directory — ' +
      '<b>python -m ganglion.console serve</b> or <b>python -m http.server -d web 8767</b>.<br><br>' +
      'No server available? <b>python tools/cli_docs/build.py --standalone out.html</b> inlines ' +
      'everything into one file that opens over <b>file://</b>.</div>';
  }

  // `build.py --standalone` embeds the spec, so that single file needs no
  // server and works over file://. The served page has no global and fetches.
  if (window.__GANGLION_CLI__) {
    boot(window.__GANGLION_CLI__);
  } else {
    fetch('./assets/cli.json', { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(boot)
      .catch(function (error) { fail(error.message); });
  }
})();
