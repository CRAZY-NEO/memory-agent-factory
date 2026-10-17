const {test} = require('node:test');
const assert = require('node:assert/strict');
const {factoryJobOutcome: outcome, factoryDraftOutcome: draftOutcome} = require('../agent_factory/web/job-status.js');
test('queue completion never turns blocked or unverified code into approved output', () => {
  for (const status of ['blocked', 'rejected', 'migration_required', 'draft', 'draft_failed']) {
    const result = outcome({status:'succeeded', kind:'component_ensure', result:{status}});
    assert.equal(result.state, status);
    assert.notEqual(result.tone, 'success');
    assert.notEqual(result.label, '已完成');
  }
});
test('planning a new task is not code generation', () => {
  const result = outcome({status:'succeeded', kind:'component_plan', result:{action:'draft'}});
  assert.equal(result.state, 'planned');
});
test('invalid customer data is still a successful component execution', () => {
  const result = outcome({status:'succeeded', kind:'component_invoke', result:{output:{valid:false}}});
  assert.equal(result.label, '试用已完成'); assert.equal(result.tone, 'success');
});
test('transport and execution failures take priority over stale domain output', () => {
  assert.equal(outcome({status:'failed', result:{status:'active'}}).tone, 'danger');
  assert.equal(outcome({status:'running', result:{status:'draft'}}).state, 'running');
});
test('example mismatch is not mislabelled as a runtime or safety failure', () => {
  const value = {status:'draft_failed', technical_checks_passed:true, examples_passed:false};
  assert.equal(outcome({status:'succeeded', result:value}).label, '示例结果不符');
  assert.equal(draftOutcome({status:'draft_failed', checks:{technical_passed:true, passed:false}}).label, '示例结果不符');
  assert.equal(draftOutcome({status:'draft_failed', checks:{technical_passed:false}}).label, '候选检查未通过');
});
test('interrupted repair does not present previous checks as a completed candidate', () => {
  const result = draftOutcome({status:'draft_failed', error:'model stopped', checks:{technical_passed:true, examples_passed:true}});
  assert.equal(result.label, '生成或修复中断'); assert.equal(result.tone, 'danger');
});
test('unresolved specification review is shown before any code-generation claim', () => {
  const result = draftOutcome({status:'draft_failed', failure_stage:'specification', error:'conflicting examples'});
  assert.equal(result.label, '规格需要澄清'); assert.equal(result.tone, 'warning');
});
