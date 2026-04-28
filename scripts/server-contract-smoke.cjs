const assert = require('node:assert/strict');

const { csvValues, profileCreateAccessError } = require('../server.cjs');

assert.deepEqual(csvValues('111, 222,,333 '), ['111', '222', '333']);

assert.equal(
  profileCreateAccessError({
    relayType: 'codex',
    channelId: '',
    allowDms: false,
    operatorIds: '111',
    allowedUserIds: '',
  }),
  'channelId is required for Codex unless allowDms is true with an approved user'
);

assert.equal(
  profileCreateAccessError({
    relayType: 'codex',
    channelId: '',
    allowDms: true,
    operatorIds: '111',
    allowedUserIds: '',
  }),
  ''
);

assert.equal(
  profileCreateAccessError({
    relayType: 'claude',
    channelId: '',
    allowDms: false,
    operatorIds: '111',
    allowedUserIds: '',
  }),
  ''
);

assert.equal(
  profileCreateAccessError({
    relayType: 'claude',
    channelId: '',
    allowDms: true,
    operatorIds: '',
    allowedUserIds: '',
  }),
  'allowDms requires at least one approved user or operator id'
);

assert.equal(
  profileCreateAccessError({
    relayType: 'codex',
    channelId: '123',
    allowDms: false,
    operatorIds: '',
    allowedUserIds: '',
  }),
  ''
);

console.log('server contract smoke passed');
