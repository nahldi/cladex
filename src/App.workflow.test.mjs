import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
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

  const html = renderToString(React.createElement(app.default));
  assert.match(html, /ClaDex|CLADEX/);
  assert.match(html, /Unified Relay Network/);

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

  assert.equal(canStartFixReviewForJob({ status: 'completed_with_warnings', severityCounts: { high: 1, medium: 0, low: 0 } }), true);
  assert.equal(canStartFixReviewForJob({ status: 'completed_with_warnings', severityCounts: { high: 0, medium: 0, low: 0 } }), false);
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

  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:3000/',
  });
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  const previousActEnvironment = globalThis.IS_REACT_ACT_ENVIRONMENT;
  const hadNavigator = Object.prototype.hasOwnProperty.call(globalThis, 'navigator');
  const previousNavigator = globalThis.navigator;
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(globalThis, 'navigator', {
    value: dom.window.navigator,
    configurable: true,
  });
  const requestedPaths = [];
  globalThis.fetch = async (url) => {
    const target = new URL(String(url), 'http://127.0.0.1:3001');
    requestedPaths.push(target.pathname);
    const payloadByPath = {
      '/api/profiles': [],
      '/api/projects': [],
      '/api/runtime-info': {
        apiBase: 'http://127.0.0.1:3001/api',
        backendDir: 'backend',
        frontendDir: 'dist',
        packaged: false,
        appVersion: 'test',
        remoteAccessProtected: true,
      },
      '/api/reviews': [],
      '/api/fix-runs': [],
      '/api/backups': [],
    };
    if (!Object.prototype.hasOwnProperty.call(payloadByPath, target.pathname)) {
      return new Response(JSON.stringify({ error: `unexpected API path: ${target.pathname}` }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    const payload = payloadByPath[target.pathname];
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  const rootElement = dom.window.document.getElementById('root');
  dom.window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
    get: () => () => {},
    set: () => true,
  });
  const mountedRoot = createRoot(rootElement);
  try {
    await act(async () => {
      mountedRoot.render(React.createElement(app.default));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    assert.match(rootElement.textContent, /Unified Relay Network/);
    assert.match(rootElement.textContent, /Review Swarm/);
    assert.deepEqual([...new Set(requestedPaths)].sort(), [
      '/api/backups',
      '/api/fix-runs',
      '/api/profiles',
      '/api/projects',
      '/api/reviews',
      '/api/runtime-info',
    ]);
  } finally {
    await act(async () => {
      mountedRoot.unmount();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    dom.window.close();
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
    globalThis.fetch = previousFetch;
    globalThis.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    if (hadNavigator) {
      Object.defineProperty(globalThis, 'navigator', { value: previousNavigator, configurable: true });
    } else {
      delete globalThis.navigator;
    }
  }

  console.log('frontend workflow smoke passed');
} finally {
  await server.close();
}
