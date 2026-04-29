import assert from 'node:assert/strict';
import { createServer } from 'vite';

const server = await createServer({
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
});

try {
  const app = await server.ssrLoadModule('/src/App.tsx');
  const {
    canBrowseReviewFindings,
    canStartFixReviewForJob,
    isPartialCancelledReview,
    refreshStatusMessage,
    reviewAgentVisibility,
    reviewDisplayStatus,
    reviewFindingTotal,
  } = app;

  const cancelledPartial = {
    status: 'cancelled',
    severityCounts: { high: 1, medium: 1, low: 0 },
    progress: { total: 4, queued: 0, running: 0, done: 2, failed: 0, cancelled: 2 },
    agents: [
      { id: 'agent-01', status: 'done', findings: 2, assignedFiles: 3 },
      { id: 'agent-02', status: 'cancelled', findings: 0, assignedFiles: 3 },
    ],
  };

  assert.equal(reviewFindingTotal(cancelledPartial), 2);
  assert.equal(isPartialCancelledReview(cancelledPartial), true);
  assert.equal(reviewDisplayStatus(cancelledPartial), 'partial/cancelled');
  assert.equal(canBrowseReviewFindings(cancelledPartial), true);
  assert.equal(canStartFixReviewForJob(cancelledPartial), false);

  const cancelledEmpty = {
    status: 'cancelled',
    severityCounts: { high: 0, medium: 0, low: 0 },
    progress: { total: 4, queued: 0, running: 0, done: 0, failed: 0, cancelled: 4 },
    agents: [],
  };

  assert.equal(isPartialCancelledReview(cancelledEmpty), false);
  assert.equal(reviewDisplayStatus(cancelledEmpty), 'cancelled');
  assert.equal(canBrowseReviewFindings(cancelledEmpty), false);

  assert.equal(canStartFixReviewForJob({ status: 'completed_with_warnings' }), true);
  assert.equal(canBrowseReviewFindings({ status: 'failed' }), true);

  const lanes = Array.from({ length: 50 }, (_, index) => ({
    id: `agent-${String(index + 1).padStart(2, '0')}`,
    provider: 'codex',
    status: index % 3 === 0 ? 'done' : 'queued',
    findings: index,
    assignedFiles: index + 1,
  }));
  const visible = reviewAgentVisibility(lanes, 8);
  assert.equal(visible.total, 50);
  assert.equal(visible.visible.length, 8);
  assert.equal(visible.overflow.length, 42);
  assert.equal(visible.overflow.at(-1).id, 'agent-50');

  const message = refreshStatusMessage([
    { label: 'reviews', error: new Error('CLADEX API request timed out after 8s.') },
    { label: 'fix runs', error: new Error('Request failed') },
    { label: 'backups', error: new Error('Request failed') },
    { label: 'runtime', error: new Error('Request failed') },
  ]);
  assert.match(message, /^Partial refresh: reviews:/);
  assert.match(message, /1 more$/);

  console.log('frontend workflow smoke passed');
} finally {
  await server.close();
}
