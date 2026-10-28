# prompts/

The fixed texts that the experiments sent to the language models.

- `system/wsvf_system.txt`: system message of WS and VF.
- `system/react_system.txt`: system message of ReAct.
- `system/no_think_prefix_wsvf.txt`: line placed before the WS and VF system message.
- `system/no_think_prefix_react.txt`: line placed before the ReAct system message.
- `system/no_think_suffix.txt`: line appended to the last user message.
- `common/model_independent_instructions.txt`: instructions shared by WS and VF.
- `common/problem_rules.txt`: problem rules of all strategies.
- `common/semantic_interpretation.txt`: instructions for reading the L1 or L2 text.
- `ws/instructions.txt`: WS instructions.
- `ws/optimizer_init_{axbo,mads,mixed_ga,random}.txt`: how each optimizer uses the warm starts.
- `ws/output_description.txt`: description of the WS output.
- `ws/output_schema_in_user_message.json`: WS output schema shown to the model.
- `vf/instructions.txt`: VF instructions.
- `vf/what_happens_next_{axbo,mads,mixed_ga,random}.txt`: what each optimizer does with the VF decision.
- `vf/search_behavior_{axbo,mads,mixed_ga,random}.txt`: how each optimizer searches the released variables.
- `vf/compact_json_contract.json`: VF output contract.
- `vf/output_schema_in_user_message.json`: VF output schema shown to the model.
- `react/action_instructions.txt`: ReAct action instructions.
- `react/output_schema_in_user_message.json`: ReAct output schema shown to the model.
- `schemas/response_format_{WarmStartOutput,VariableFixingOutput,ReActAction}.json`: JSON schema of the `response_format` field of each request.
- `semantic/L1.txt`: L1 text, the same in all four scenarios.
- `semantic/L2.txt`: L2 text, the same in all four scenarios.

The WS output limits allocation_rationale to 60 words, and a longer reply is retried once.
