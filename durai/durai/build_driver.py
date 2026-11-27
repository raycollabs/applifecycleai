"""Sample: Run component S-TRADE-BOOK with a scripted model. Check a receipt with stages, tokens, cost and stop reason FINISHED."""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .build_guidance import ENVIRONMENT_VARIABLE
from .build_tools import BuildTools
from .model_pricing import estimate_costs
from .worker_evidence import retain_worker_trace

ROOT = Path(__file__).resolve().parents[1]
INPUT_RATE = 2.0 / 1_000_000
OUTPUT_RATE = 10.0 / 1_000_000
CALLS_PER_COMPONENT = 80
CALLS_TOTAL = 400
COST_CAP = 10.0
RETRY_WAITS = (10, 30)
NUDGE_LIMIT = 2
KEEP_RECENT = 10
OLD_RESULT_CHARS = 1500
STALL_NUDGE_LIMIT = 30
STALL_STOP_LIMIT = 50

SYSTEM_PROMPT = (
    'You are a software engineer who builds one component of an application from a request written by a user. '
    'You act only through the tools you are given. You have no other source of information. '
    'Call get_guidance first and follow it. Put design text in documents. In code, write only sample walkthrough comments.')


def estimate_cost(input_tokens, output_tokens):
    # Sample: 1000000 input tokens and 100000 output tokens cost 3.0 USD at 2 and 10 USD per million.
    return round(input_tokens * INPUT_RATE + output_tokens * OUTPUT_RATE, 6)


def component_prompt(sample, enhanced=False, bare=False):
    # Sample: Sample S-TRADE-BOOK in bare mode returns the raw request text without Durai stage steps. With bare False returns the 7 steps.
    if bare:
        return (
            'Component ' + sample['sampleId'] + ' (' + sample['title'] + ')\n'
            'The request of the user:\n' + sample['requestText'] + '\n\n'
            'Build this component. Write the code and tests. Fix the code until all tests pass, then call finish.\n'
            'The tests read the database URL from the environment variable ' + ENVIRONMENT_VARIABLE + '. '
            'Earlier components may already be in the workspace. Call list_files to see them. Extend them and keep them working.')
    extra = ('After step 1 call get_enhanced_request once and use its questions, concepts and answer formats. ' if enhanced else '')
    return (
        'Component ' + sample['sampleId'] + ' (' + sample['title'] + ')\n'
        'The request of the user:\n' + sample['requestText'] + '\n\n'
        'This request is already selected. Do these steps in order.\n'
        '1. Call get_guidance and read it. ' + extra + '\n'
        '2. Write docs/design.md and call submit_stage with stage DESIGN.\n'
        '3. Write docs/plan.md with phases and a test table (class, function, goal). Call submit_stage with stage PLAN.\n'
        '4. Write the code and the tests.\n'
        '5. Call run_tests. Fix the code until all tests pass.\n'
        '6. Call submit_stage with stage IMPLEMENTATION, then submit_stage with stage TESTS.\n'
        '7. Call finish.\n'
        'The tests read the database URL from the environment variable ' + ENVIRONMENT_VARIABLE + '. '
        'Earlier components may already be in the workspace. Call list_files to see them. Extend them and keep them working.')


def tool_specs(tools):
    # Sample: Convert 10 registry tools to 10 function specs with name, description and parameters.
    return [{'type': 'function', 'function': {'name': item['name'], 'description': item['description'],
                                              'parameters': item['inputSchema']}} for item in tools.tool_list()]


def shrink(messages, keep=KEEP_RECENT, manifest=None):
    # Sample: Keep the last 10 messages whole. Cut each older tool result to 1500 characters. With keep 40, keep the last 40 whole. If manifest is given and messages exceeds keep, pin manifest at index 2.
    cut = len(messages) - keep
    result = []
    for index, message in enumerate(messages):
        if index < cut and isinstance(message, ToolMessage) and len(str(message.content)) > OLD_RESULT_CHARS:
            message = ToolMessage(content=str(message.content)[:OLD_RESULT_CHARS] + '\n[older output cut]',
                                  tool_call_id=message.tool_call_id)
        result.append(message)
    if manifest is not None and len(messages) > keep:
        pos = min(2, len(result))
        result.insert(pos, manifest)
    return result


def utc():
    # Sample: Return the current UTC time as an ISO text.
    return datetime.now(timezone.utc).isoformat()


class BlindBuild:
    def __init__(self, tools, model, label, provider, model_name, receipts, calls_per_component=CALLS_PER_COMPONENT,
                 calls_total=CALLS_TOTAL, cost_cap=COST_CAP, sleep=time.sleep, keep_recent=KEEP_RECENT,
                 stall_nudge_limit=STALL_NUDGE_LIMIT, stall_stop_limit=STALL_STOP_LIMIT, bare=False,
                 fallback_model=None, fallback_provider=None, fallback_model_name=None):
        # Sample: Bind tools, model, label L-1 and caps 80, 400 and 10 USD. Keep the last 10 messages whole. Nudge at 30 stall calls, stop at 50. With bare True disable Durai stages and gates.
        self.tools, self.model, self.label = tools, model, label
        self.keep_recent = int(keep_recent)
        self.stall_nudge_limit = int(stall_nudge_limit)
        self.stall_stop_limit = int(stall_stop_limit)
        self.bare = bool(bare)
        self.provider, self.model_name, self.receipts = provider, model_name, Path(receipts)
        self.primary_provider = provider
        self.fallback_model, self.fallback_provider, self.fallback_model_name = fallback_model, fallback_provider, fallback_model_name
        self.fallback_used = False
        self.per_component, self.total_cap, self.cost_cap, self.sleep = calls_per_component, calls_total, cost_cap, sleep
        self.calls = self.input_tokens = self.output_tokens = 0
        self.bound = None

    def cost(self):
        # Sample: Return the cost of all tokens so far at the enterprise rate.
        return estimate_costs(self.provider, self.model_name, self.input_tokens, self.output_tokens)['enterprise']

    def manifest(self, sample_id):
        # Sample: Return a system message summarizing files in workspace and accepted stages. In bare mode return None.
        if self.bare or not sample_id or sample_id not in self.tools.components:
            return None
        accepted = [s['stage'] for s in self.tools.components[sample_id]['stages']]
        files = self.tools.list_files()
        listing = files if len(files) < 2000 else files[:2000] + '\n[truncated]'
        return SystemMessage(content=(
            "Workspace State Manifest:\n"
            f"Accepted Stages: {accepted or 'None'}\n"
            f"Current Workspace Files:\n{listing}\n"
            "Do not re-read files unless you need their exact content."
        ))

    def invoke(self, messages, sample_id=None):
        # Sample: A provider error happens. Wait 10 s and 30 s. Retry. After the third failure return the error text.
        if self.bound is None:
            self.bound = self.model.bind_tools(tool_specs(self.tools))
        errors = 0
        manifest_msg = self.manifest(sample_id)
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                return self.bound.invoke(shrink(messages, self.keep_recent, manifest=manifest_msg)), errors, None
            except Exception as error:
                errors += 1
                text = type(error).__name__ + ': ' + str(error)[:300]
                if attempt < len(RETRY_WAITS):
                    self.sleep(RETRY_WAITS[attempt])
        if self.fallback_model is not None:
            self.model = self.fallback_model
            self.bound = self.model.bind_tools(tool_specs(self.tools))
            self.provider = self.fallback_provider or self.provider
            self.model_name = self.fallback_model_name or self.model_name
            self.fallback_model = None
            self.fallback_used = True
            try:
                return self.bound.invoke(shrink(messages, self.keep_recent, manifest=manifest_msg)), errors, None
            except Exception as error:
                errors += 1
                text = type(error).__name__ + ': ' + str(error)[:300]
        return None, errors, text

    def run_component(self, sample_id):
        # Sample: Run S-TRADE-BOOK. Stop at the 80th call with STOP_BUDGET. Write receipts/blind-build-L-1-S-TRADE-BOOK.json.
        tools = self.tools
        tools.get_sample(sample_id)
        tools.finished = False
        sample = tools.components[sample_id]['sample']
        errors_before = dict(tools.errors)
        started, calls, input_tokens, output_tokens, provider_errors, nudges = utc(), 0, 0, 0, 0, 0
        messages = [
            SystemMessage(content=SYSTEM_PROMPT if not self.bare else 'You are a software engineer who builds software. Act only through tools.'),
            HumanMessage(content=component_prompt(sample, 'get_enhanced_request' in tools.table, bare=self.bare))
        ]
        stop, detail = None, None

        traces_folder = (self.receipts.parent / 'traces') if hasattr(self, 'receipts') else (ROOT / 'private/traces')
        traces_folder.mkdir(parents=True, exist_ok=True)
        stream_path = traces_folder / ('stream-' + self.label + '-' + sample_id + '.jsonl')
        stream_file = stream_path.open('a', encoding='utf-8')

        def log_msg(msg):
            entry = {'role': msg.type, 'content': msg.content, 'at': utc()}
            for attr in ('tool_calls', 'tool_call_id', 'name', 'status'):
                val = getattr(msg, attr, None)
                if val:
                    entry[attr] = val
            usage = getattr(msg, 'usage_metadata', None)
            if usage:
                entry['usage'] = usage
            stream_file.write(json.dumps(entry, default=str) + '\n')
            stream_file.flush()

        for m in messages:
            log_msg(m)

        changed_paths = set()
        calls_since_last_stage = 0
        last_stage_count = len(tools.components[sample_id]['stages'])
        recent_tools = []
        stall_nudged = False

        try:
            while stop is None:
                if calls >= self.per_component or self.calls >= self.total_cap:
                    stop = 'STOP_BUDGET'
                    break
                if self.cost() >= self.cost_cap:
                    stop = 'STOP_COST'
                    break

                if not self.bare:
                    current_stages = len(tools.components[sample_id]['stages'])
                    if current_stages > last_stage_count:
                        last_stage_count = current_stages
                        calls_since_last_stage = 0
                        stall_nudged = False
                    elif calls > 0:
                        calls_since_last_stage += 1

                    if calls_since_last_stage >= self.stall_nudge_limit and not stall_nudged:
                        stall_nudged = True
                        accepted = [s['stage'] for s in tools.components[sample_id]['stages']]
                        next_stage = ('DESIGN' if 'DESIGN' not in accepted else
                                      'PLAN' if 'PLAN' not in accepted else
                                      'IMPLEMENTATION' if 'IMPLEMENTATION' not in accepted else
                                      'TESTS' if 'TESTS' not in accepted else 'finish')
                        nudge_msg = HumanMessage(
                            content='Notice: {} calls have run without a stage advance. Accepted stages: {}. '
                                    'Advance to stage {} or call finish.'.format(
                                        calls_since_last_stage, accepted or 'none', next_stage))
                        messages.append(nudge_msg)
                        log_msg(nudge_msg)

                    if calls_since_last_stage >= self.stall_stop_limit:
                        stop = 'STOP_STALL'
                        accepted = [s['stage'] for s in tools.components[sample_id]['stages']]
                        detail = {
                            'lastStage': accepted[-1] if accepted else 'NONE',
                            'callsSinceLastStage': calls_since_last_stage,
                            'recentTools': recent_tools[-5:]
                        }
                        break

                reply, failed, detail = self.invoke(messages, sample_id)
                provider_errors += failed
                if reply is None:
                    stop = 'PROVIDER_ERROR'
                    break
                usage = getattr(reply, 'usage_metadata', None) or {}
                calls += 1
                self.calls += 1
                input_tokens += int(usage.get('input_tokens') or 0)
                output_tokens += int(usage.get('output_tokens') or 0)
                self.input_tokens += int(usage.get('input_tokens') or 0)
                self.output_tokens += int(usage.get('output_tokens') or 0)
                messages.append(reply)
                log_msg(reply)
                if not reply.tool_calls:
                    if nudges >= NUDGE_LIMIT:
                        stop = 'STOP_IDLE'
                        break
                    nudges += 1
                    idle_msg = HumanMessage(content='Your last reply had no tool call. Call a tool now. Call finish when the work is done.')
                    messages.append(idle_msg)
                    log_msg(idle_msg)
                    continue
                for call in reply.tool_calls:
                    recent_tools.append(call['name'])
                    if len(recent_tools) > 10:
                        recent_tools.pop(0)
                    outcome = tools.call(call['name'], call.get('args') or {})
                    if call['name'] in ('write_file', 'edit_file') and not str(outcome.get('text', '')).startswith('ERROR:'):
                        path_arg = (call.get('args') or {}).get('path')
                        if path_arg:
                            changed_paths.add(str(path_arg))
                    tool_status = 'error' if outcome.get('isError') or str(outcome.get('text', '')).startswith('ERROR:') else 'success'
                    tool_msg = ToolMessage(content=outcome['text'], tool_call_id=call['id'], status=tool_status)
                    messages.append(tool_msg)
                    log_msg(tool_msg)
                if tools.finished:
                    stop = 'FINISHED'
        finally:
            stream_file.close()

        evidence_root = self.receipts.parent / 'worker-evidence' if hasattr(self, 'receipts') else ROOT / 'private/worker-evidence'
        retained = retain_worker_trace({'digest': sample['digest']}, 'blind-' + self.label + '-' + sample_id,
                                       messages, changed_paths, root=evidence_root)
        state = tools.components[sample_id]
        pricing = estimate_costs(self.provider, self.model_name, input_tokens, output_tokens)
        receipt = {
            'label': self.label, 'sampleId': sample_id, 'revision': sample['revision'], 'digest': sample['digest'],
            'model': self.model_name, 'provider': self.provider, 'primaryProvider': self.primary_provider,
            'fallbackUsed': self.fallback_used, 'startedAt': started, 'finishedAt': utc(),
            'bare': self.bare,
            'calls': calls, 'inputTokens': input_tokens, 'outputTokens': output_tokens,
            'estimatedEnterpriseCost': pricing['enterprise'],
            'estimatedListCost': pricing['list'],
            'pricingRates': pricing['rates'],
            'stages': state['stages'], 'lastRunTests': state['lastRunTests'], 'stopReason': stop,
            'stopDetail': detail if stop in ('PROVIDER_ERROR', 'STOP_STALL') else None,
            'providerErrors': provider_errors, 'nudges': nudges,
            'keepRecent': self.keep_recent,
            'tracePath': retained['path'], 'traceDigest': retained['digest'], 'messageCount': retained['messageCount'],
            'toolErrors': {name: count - errors_before.get(name, 0) for name, count in tools.errors.items()
                           if count - errors_before.get(name, 0)},
        }
        self.receipts.mkdir(parents=True, exist_ok=True)
        (self.receipts / ('blind-build-' + self.label + '-' + sample_id + '.json')).write_text(
            json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        return receipt

    def run(self, components):
        # Sample: Run S-TRADE-BOOK then S-TRADE-ACCESS. Stop the whole build after a PROVIDER_ERROR, STOP_COST or total STOP_BUDGET. Write the summary.
        receipts = []
        for sample_id in components:
            receipts.append(self.run_component(sample_id))
            if receipts[-1]['stopReason'] in ('PROVIDER_ERROR', 'STOP_COST') or self.calls >= self.total_cap:
                break
        total_pricing = estimate_costs(self.provider, self.model_name, self.input_tokens, self.output_tokens)
        summary = {'label': self.label, 'provider': self.provider, 'model': self.model_name, 'calls': self.calls,
                   'bare': self.bare,
                   'inputTokens': self.input_tokens, 'outputTokens': self.output_tokens,
                   'estimatedEnterpriseCost': total_pricing['enterprise'],
                   'estimatedListCost': total_pricing['list'],
                   'pricingRates': total_pricing['rates'],
                   'components': [{'sampleId': item['sampleId'], 'stopReason': item['stopReason'], 'calls': item['calls'],
                                   'lastRunTests': item['lastRunTests'], 'stages': [s['stage'] for s in item['stages']]}
                                  for item in receipts]}
        (self.receipts / ('blind-build-' + self.label + '-summary.json')).write_text(
            json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        return summary


class ScriptedModel:
    def __init__(self, script=None):
        # Sample: Hold a script of tool call lists. With no script run the full smoke script for the component in the prompt.
        self.script = script

    def bind_tools(self, specs):
        # Sample: Accept the tool specs. Return the same model.
        return self

    def invoke(self, messages):
        # Sample: Count the earlier AI replies n. Return script step n as tool calls with 100 input and 10 output tokens.
        step = sum(1 for message in messages if isinstance(message, AIMessage))
        script = self.script if self.script is not None else smoke_script(messages)
        calls = script[step] if step < len(script) else [('finish', {})]
        return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': 'call-' + str(step) + '-' + str(index)}
                                                 for index, (name, args) in enumerate(calls)],
                         usage_metadata={'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110})


def smoke_script(messages):
    # Sample: Prompt names S-TRADE-BOOK. Return steps that write design, plan, one module and one test, run tests and finish.
    ident = 'S-TRADE-BOOK'
    for msg in messages:
        match = re.search(r'Component (S-[A-Z0-9-]+)', str(getattr(msg, 'content', '')))
        if match:
            ident = match.group(1)
            break
    slug = ident.lower().replace('-', '_')
    test = ('import os\nimport unittest\nimport psycopg\n\n\nclass T(unittest.TestCase):\n'
            '    def test_database_answers(self):\n'
            '        # Sample: Connect. Select 1. Check 1.\n'
            '        with psycopg.connect(os.environ["' + ENVIRONMENT_VARIABLE + '"]) as c:\n'
            '            self.assertEqual(1, c.execute("SELECT 1").fetchone()[0])\n')
    return [
        [('get_guidance', {})],
        [('write_file', {'path': 'docs/design-' + slug + '.md', 'content': 'Design for ' + ident + '\n'})],
        [('submit_stage', {'stage': 'DESIGN', 'paths': ['docs/design-' + slug + '.md']})],
        [('write_file', {'path': 'docs/plan-' + slug + '.md', 'content': 'Plan for ' + ident + '\n'})],
        [('submit_stage', {'stage': 'PLAN', 'paths': ['docs/plan-' + slug + '.md']})],
        [('write_file', {'path': 'src/' + slug + '.py', 'content': '# Sample: Return 1.\nVALUE = 1\n'}),
         ('write_file', {'path': 'tests/test_' + slug + '.py', 'content': test})],
        [('run_tests', {})],
        [('submit_stage', {'stage': 'IMPLEMENTATION', 'paths': ['src/' + slug + '.py']})],
        [('submit_stage', {'stage': 'TESTS', 'paths': ['tests/test_' + slug + '.py']})],
        [('finish', {})],
    ]


def live_model(provider, role='execution', thinking=None):
    # Sample: Build the DeepSeek execution model with 32768 output tokens, 600 s timeout, streaming and thinking. Pass thinking False to disable thinking on failover.
    from .provider_config import chat_model, coding_limits, load
    config = load(provider)
    limits = coding_limits(provider, config['baseUrl'])
    use_thinking = limits['thinking'] if thinking is None else bool(thinking)
    overrides = {'max_tokens': limits['maxTokens'], 'timeout': limits['timeoutSeconds'], 'streaming': bool(limits['streaming'])}
    if limits['streaming']:
        overrides['stream_usage'] = True
    if limits.get('reasoningEffort') and use_thinking:
        overrides['reasoning_effort'] = limits['reasoningEffort']
    if use_thinking:
        overrides['extra_body'] = {'thinking': {'type': 'enabled'}}
    else:
        overrides['temperature'] = 0
        if limits['thinking']:
            overrides['extra_body'] = {'thinking': {'type': 'disabled'}}
    return chat_model(provider, role, **overrides), config['models'][role]
