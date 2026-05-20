/* ============================================================
   GANGLION OPERATOR CONSOLE  ·  mock data
   shapes mirror ganglion/contract, analyzer/, runs/*.json
   ============================================================ */

window.GANGLION = (function () {
  const now = new Date('2026-05-21T14:32:17+09:00');
  const ts = (offsetSec) => {
    const d = new Date(now.getTime() + offsetSec * 1000);
    return d.toISOString().slice(11, 19) + '.' +
      String(d.getUTCMilliseconds()).padStart(3, '0');
  };

  // --- catalogs ------------------------------------------------
  const catalogs = [
    {
      id: 'iot_light_5',
      version: 7,
      allow_empty_calls: false,
      tools: 5,
      published_at: '2026-05-19T08:14:02Z',
      tools_list: [
        { name: 'set_light', args: [
            { name: 'room',  kind: 'EnumArg', values: ['living','bedroom','kitchen','bath'], aliases: { '거실':'living','안방':'bedroom','부엌':'kitchen','화장실':'bath' } },
            { name: 'state', kind: 'EnumArg', values: ['on','off'], aliases: { '켜':'on','꺼':'off','켜줘':'on','꺼줘':'off' } },
            { name: 'brightness', kind: 'IntArg', range: [0, 100], required: false },
        ]},
        { name: 'set_color', args: [
            { name: 'room',  kind: 'EnumArg', values: ['living','bedroom','kitchen','bath'] },
            { name: 'color', kind: 'StringArg', aliases: { '주황':'orange','빨강':'red','파랑':'blue' } },
        ]},
        { name: 'dim_light', args: [
            { name: 'room', kind: 'EnumArg', values: ['living','bedroom','kitchen','bath'] },
            { name: 'level', kind: 'IntArg', range: [0, 100] },
        ]},
        { name: 'set_scene', args: [
            { name: 'scene', kind: 'EnumArg', values: ['movie','sleep','focus','party'], aliases: { '영화':'movie','수면':'sleep','집중':'focus','파티':'party' } },
        ]},
        { name: 'schedule_light', args: [
            { name: 'room', kind: 'EnumArg', values: ['living','bedroom','kitchen','bath'] },
            { name: 'at',   kind: 'TimeArg' },
            { name: 'state', kind: 'EnumArg', values: ['on','off'] },
        ]},
      ],
    },
    { id: 'home_iot_20', version: 4, allow_empty_calls: false, tools: 20, published_at: '2026-05-18T11:02:48Z' },
    { id: 'smart_home_50', version: 2, allow_empty_calls: false, tools: 50, published_at: '2026-05-15T09:30:00Z' },
    { id: 'bfcl_v4_simple', version: 1, allow_empty_calls: true, tools: 'per-case', published_at: '2026-05-16T18:44:11Z' },
  ];

  // --- pipeline state ------------------------------------------
  const pipeline = {
    catalog_id: 'iot_light_5',
    iteration: 6,
    max_iter: 12,
    threshold: 0.93,
    auto_apply: false,
    plateau_K: 3,
    started_at: '2026-05-20T22:11:08Z',
    em_curve: [0.612, 0.701, 0.748, 0.802, 0.847, 0.862, 0.871],
    am_curve: [0.682, 0.749, 0.791, 0.834, 0.881, 0.892, 0.901],
    ast_curve: [0.594, 0.681, 0.728, 0.778, 0.819, 0.835, 0.847],
    stages: [
      { id: 'synth',     name: 'lm_data_synth',       state: 'done',     out: 'lm.synth.completed',         since: '01:14' },
      { id: 'finetune',  name: 'lm_finetune',         state: 'done',     out: 'lm.finetune.completed',      since: '00:48' },
      { id: 'bench-iot', name: 'benchmark_iot',       state: 'done',     out: 'benchmark.iot.completed',    since: '00:22' },
      { id: 'bench-bfcl',name: 'benchmark_bfcl',      state: 'done',     out: 'benchmark.bfcl.completed',   since: '00:18' },
      { id: 'trace',     name: 'analyzer_trace_store',state: 'streaming',out: 'analyzer.trace.recorded',    since: '00:00' },
      { id: 'taxonomy',  name: 'analyzer_failure_taxonomy', state: 'streaming', out: 'analyzer.failure.classified', since: '00:00' },
      { id: 'metrics',   name: 'analyzer_metrics',    state: 'pending',  out: 'analyzer.metrics.summarized',since: null },
      { id: 'rules',     name: 'analyzer_rule_synthesis', state: 'pending', out: 'analyzer.rule.proposed', since: null },
      { id: 'patch',     name: 'contract_catalog',    state: 'pending',  out: 'contract.catalog.published', since: null },
    ],
    pending_patches: 3,
  };

  // --- abort reasons (Principle: ranked stop conditions) -------
  const abortReasons = [
    { rank: 1, key: 'threshold_reached',   desc: 'exact_match_rate ≥ threshold', count: 4, color: 'chartreuse' },
    { rank: 2, key: 'max_iter_reached',    desc: 'iteration ≥ max_iter',         count: 1, color: 'amber'      },
    { rank: 3, key: 'plateau',             desc: 'no improvement K=3',            count: 2, color: 'amber'      },
    { rank: 4, key: 'patch_apply_failed',  desc: 'contract_catalog rejected',    count: 0, color: 'vermillion' },
    { rank: 5, key: 'primitive_failed',    desc: 'lm.synth | finetune | bench',  count: 1, color: 'vermillion' },
    { rank: 6, key: 'primitive_timeout',   desc: 'window W elapsed',             count: 0, color: 'vermillion' },
    { rank: 7, key: 'protocol_violation',  desc: 'missing catalog_id / schema',  count: 0, color: 'vermillion' },
  ];

  // --- 14-bucket failure taxonomy ------------------------------
  const failureTaxonomy = [
    { id: 'F01', name: 'unknown_tool',           count: 27, kind: 'fail' },
    { id: 'F02', name: 'missing_required_arg',   count: 41, kind: 'fail' },
    { id: 'F03', name: 'invalid_enum_value',     count: 84, kind: 'fail' },
    { id: 'F04', name: 'alias_unresolved',       count: 62, kind: 'fail' },
    { id: 'F05', name: 'type_mismatch',          count: 19, kind: 'fail' },
    { id: 'F06', name: 'out_of_range',           count: 12, kind: 'warn' },
    { id: 'F07', name: 'malformed_dsl',          count: 33, kind: 'fail' },
    { id: 'F08', name: 'json_parse_error',       count: 8,  kind: 'fail' },
    { id: 'F09', name: 'empty_calls_when_required', count: 14, kind: 'warn' },
    { id: 'F10', name: 'extra_unknown_arg',      count: 23, kind: 'warn' },
    { id: 'F11', name: 'parallel_count_mismatch',count: 17, kind: 'warn' },
    { id: 'F12', name: 'argument_order',         count: 5,  kind: 'warn' },
    { id: 'F13', name: 'repair_exhausted',       count: 9,  kind: 'fail' },
    { id: 'F14', name: 'graded_partial',         count: 38, kind: 'warn' },
  ];

  // --- traces (subset of trace_store) --------------------------
  const traces = [
    { id: 'T-2206', case: 'iot_light:case_0142', failure: 'F04', repair: 1, t: '14:30:12.118',
      prompt: '거실 불 켜줘',
      expected: { calls: [{ tool: 'set_light', args: { room: 'living', state: 'on' } }] },
      actual:   { calls: [{ tool: 'set_light', args: { room: '거실',    state: 'on' } }] },
    },
    { id: 'T-2207', case: 'iot_light:case_0143', failure: null, repair: 0, t: '14:30:12.241',
      prompt: '안방 불 꺼줘',
      expected: { calls: [{ tool: 'set_light', args: { room: 'bedroom', state: 'off' } }] },
      actual:   { calls: [{ tool: 'set_light', args: { room: 'bedroom', state: 'off' } }] },
    },
    { id: 'T-2208', case: 'bfcl:simple_python:032', failure: 'F03', repair: 2, t: '14:30:12.318',
      prompt: 'Get weather at lat 37.5 lng 127 in metric',
      expected: { calls: [{ tool: 'get_weather', args: { lat: 37.5, lng: 127, units: 'metric' } }] },
      actual:   { calls: [{ tool: 'get_weather', args: { lat: 37.5, lng: 127, units: 'celsius' } }] },
    },
    { id: 'T-2209', case: 'bfcl:irrelevance:004', failure: 'F09', repair: 0, t: '14:30:12.402',
      prompt: 'Tell me a joke about cats',
      expected: { calls: [] },
      actual:   { calls: [{ tool: 'tell_joke', args: { topic: 'cats' } }] },
    },
    { id: 'T-2210', case: 'iot_light:case_0144', failure: 'F14', repair: 1, t: '14:30:12.488',
      prompt: '주방 조명 50% 밝기로 켜줘',
      expected: { calls: [{ tool: 'set_light', args: { room: 'kitchen', state: 'on', brightness: 50 } }] },
      actual:   { calls: [{ tool: 'set_light', args: { room: 'kitchen', state: 'on' } }] },
    },
    { id: 'T-2211', case: 'bfcl:parallel:017', failure: 'F11', repair: 0, t: '14:30:12.561',
      prompt: 'Open log A and file B simultaneously',
      expected: { calls: [{tool:'open',args:{path:'A'}},{tool:'open',args:{path:'B'}}] },
      actual:   { calls: [{tool:'open',args:{path:'A'}}] },
    },
    { id: 'T-2212', case: 'iot_light:case_0145', failure: 'F07', repair: 3, t: '14:30:12.629',
      prompt: '영화 분위기로 만들어줘',
      expected: { calls: [{ tool: 'set_scene', args: { scene: 'movie' } }] },
      actual:   '{calls: [{tool: set_scene, args: {scene: 영화}}]',
    },
  ];

  // --- proposed patches (R1-R11 patterns) ----------------------
  const proposals = [
    {
      id: 'P-014', rule: 'R3', kind: 'alias_extension',
      target: 'iot_light_5 / set_light / room',
      summary: 'Add aliases {"거실":"living","안방":"bedroom","주방":"kitchen"} to EnumArg',
      evidence: { F04: 62, F03: 12 },
      diff: {
        before: `EnumArg(values=["living","bedroom","kitchen","bath"],\n        aliases={})`,
        after:  `EnumArg(values=["living","bedroom","kitchen","bath"],\n        aliases={\n          "거실":"living",\n          "안방":"bedroom",\n          "주방":"kitchen"\n        })`,
      },
      confidence: 0.94,
    },
    {
      id: 'P-015', rule: 'R7', kind: 'enum_extension',
      target: 'bfcl_per_case / get_weather / units',
      summary: 'Extend EnumArg values to include "celsius" "fahrenheit"',
      evidence: { F03: 18 },
      diff: {
        before: `EnumArg(values=["metric","imperial"])`,
        after:  `EnumArg(values=["metric","imperial","celsius","fahrenheit"],\n        aliases={"celsius":"metric","fahrenheit":"imperial"})`,
      },
      confidence: 0.82,
    },
    {
      id: 'P-016', rule: 'R9', kind: 'null_action_allow',
      target: 'iot_light_5 / Catalog',
      summary: 'Catalog.allow_empty_calls := True (irrelevance abstention)',
      evidence: { F09: 14 },
      diff: {
        before: `Catalog(allow_empty_calls=False)`,
        after:  `Catalog(allow_empty_calls=True)`,
      },
      confidence: 0.71,
    },
  ];

  // --- event bus log -------------------------------------------
  const events = [
    { t: '14:30:12.041', mod: 'factory',    name: 'factory.pipeline.start',         pay: 'catalog=iot_light_5 iter=6 thr=0.93' },
    { t: '14:30:12.046', mod: 'lm',         name: 'lm.synth.request',               pay: 'catalog=iot_light_5 strat=tool_anchored+adversarial' },
    { t: '14:30:14.812', mod: 'lm',         name: 'lm.synth.completed',             pay: 'dataset=ds_2026_05_21_001 n=2200' },
    { t: '14:30:14.815', mod: 'lm',         name: 'lm.finetune.request',            pay: 'catalog=iot_light_5 dataset=ds_2026_05_21_001' },
    { t: '14:31:48.290', mod: 'lm',         name: 'lm.finetune.completed',          pay: 'adapter=ad_iter6 train_loss=0.142' },
    { t: '14:31:48.297', mod: 'benchmark',  name: 'benchmark.iot.request',          pay: 'adapter=ad_iter6 tier=iot_light_5' },
    { t: '14:31:48.297', mod: 'benchmark',  name: 'benchmark.bfcl.request',         pay: 'adapter=ad_iter6 category=callable' },
    { t: '14:32:06.118', mod: 'benchmark',  name: 'benchmark.iot.completed',        pay: 'n=500 em=0.871 path=runs/iot/iter6.json' },
    { t: '14:32:09.471', mod: 'benchmark',  name: 'benchmark.bfcl.completed',       pay: 'n=400 em=0.768 path=runs/bfcl/iter6.json' },
    { t: '14:32:09.503', mod: 'analyzer',   name: 'analyzer.trace.recorded',        pay: 'trace_id=T-2206 catalog=iot_light_5' },
    { t: '14:32:09.504', mod: 'analyzer',   name: 'analyzer.trace.recorded',        pay: 'trace_id=T-2207 catalog=iot_light_5' },
    { t: '14:32:09.506', mod: 'analyzer',   name: 'analyzer.trace.recorded',        pay: 'trace_id=T-2208 catalog=iot_light_5', ghost: true },
    { t: '14:32:09.514', mod: 'analyzer',   name: 'analyzer.failure.classified',    pay: 'trace=T-2206 label=F04' },
    { t: '14:32:09.518', mod: 'analyzer',   name: 'analyzer.failure.classified',    pay: 'trace=T-2208 label=F03' },
    { t: '14:32:09.521', mod: 'analyzer',   name: 'analyzer.failure.classified',    pay: 'trace=T-2209 label=F09' },
    { t: '14:32:11.044', mod: 'analyzer',   name: 'analyzer.metrics.summarized',    pay: 'em=0.871 am=0.901 ast=0.847 n=900' },
    { t: '14:32:11.812', mod: 'analyzer',   name: 'analyzer.rule.proposed',         pay: 'patch=P-014 rule=R3 conf=0.94' },
    { t: '14:32:11.844', mod: 'analyzer',   name: 'analyzer.rule.proposed',         pay: 'patch=P-015 rule=R7 conf=0.82' },
    { t: '14:32:11.901', mod: 'analyzer',   name: 'analyzer.rule.proposed',         pay: 'patch=P-016 rule=R9 conf=0.71' },
    { t: '14:32:12.017', mod: 'factory',    name: 'factory.pipeline.iterated',      pay: 'catalog=iot_light_5 iter=6 pending_patches=3' },
  ];

  // --- evaluation tuples ---------------------------------------
  const evaluations = [
    { id: 'E-088', t: '14:14:02', client: 'qwen-native', catalog: 'iot_light_5',    bench: 'iot',  opts: '--tier iot_light_5 --repeat 1',     em: 0.812, status: 'completed' },
    { id: 'E-089', t: '14:18:48', client: 'qwen',        catalog: 'iot_light_5',    bench: 'iot',  opts: '--tier iot_light_5 --repair',       em: 0.871, status: 'completed' },
    { id: 'E-090', t: '14:22:09', client: 'qwen',        catalog: 'bfcl_v4_simple', bench: 'bfcl', opts: '--bfcl simple_python --limit 100',  em: 0.768, status: 'completed' },
    { id: 'E-091', t: '14:25:22', client: 'rules',       catalog: 'iot_light_5',    bench: 'iot',  opts: '--tier iot_light_5 --limit 5',      em: 1.000, status: 'completed' },
    { id: 'E-092', t: '14:30:01', client: 'qwen-text',   catalog: 'home_iot_20',    bench: 'iot',  opts: '--tier home_iot_20 --repeat 3',     em: null,  status: 'running'   },
    { id: 'E-093', t: '14:31:18', client: 'qwen',        catalog: 'bfcl_v4_simple', bench: 'bfcl', opts: '--bfcl irrelevance --bfcl-allow-empty-calls', em: null, status: 'aborted', reason: 'metrics_failed' },
  ];

  // --- observability metrics -----------------------------------
  const obs = {
    iterations_total: { value: 47, delta: '+6', spark: [0,1,1,2,3,5,7,9,12,15,18,22,26,30,33,36,40,43,45,47] },
    uplift:           { value: 0.024, delta: '−0.011', unit: 'Δ em / iter (med)', spark: [0.089,0.090,0.052,0.054,0.054,0.045,0.015,0.024] },
    abort_rate:       { value: 0.085, delta: '−0.02', unit: 'aborted / start (30d)', spark: [0.12,0.11,0.11,0.10,0.10,0.09,0.09,0.08,0.085] },
    plateau_rate:     { value: 0.286, delta: '+0.04', unit: 'plateau / aborted', spark: [0.20,0.22,0.22,0.24,0.25,0.27,0.28,0.286] },
    protocol_violation_count: { value: 0, delta: '0', unit: 'count (rolling)', spark: [0,0,0,0,0,0,0,0,0,0,0] },
    evaluation_runs_total: { value: 312, delta: '+24', unit: 'count (lifetime)', spark: [240,254,261,272,280,290,295,302,308,312] },
    pipeline_wall_hours: { value: 4.18, delta: '+0.61', unit: 'h current run', spark: null },
    patches_applied:  { value: 17, delta: '+3 pending', unit: 'count (lifetime applied)', spark: [10,11,12,12,13,14,15,15,16,16,17] },
  };

  return {
    now, ts,
    catalogs, pipeline, abortReasons, failureTaxonomy, traces, proposals, events, evaluations, obs,
  };
})();
