/* Attendance & Leave — Workstream B, Phase B2.
 * Offline-first punch queue: IndexedDB outbox + retry on reconnect. No
 * existing offline-queue pattern exists elsewhere in the app (confirmed by
 * research before building this), so this is a self-contained addition —
 * it doesn't touch app/static/sw.js or any other shared script. */
(function () {
  var DB_NAME = 'omniflow_attendance';
  var STORE = 'queue';

  function openDB() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        req.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function queueAdd(item) {
    return openDB().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).add(item);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function queueAll() {
    return openDB().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, 'readonly');
        var req = tx.objectStore(STORE).getAll();
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function queueRemove(id) {
    return openDB().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(id);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function buildFormData(item) {
    var fd = new FormData();
    fd.append('lat', item.lat);
    fd.append('lng', item.lng);
    fd.append('out_of_fence_reason', item.reason || '');
    if (item.photoBlob) fd.append('photo', item.photoBlob, item.photoName || 'punch.jpg');
    return fd;
  }

  function postPunch(fd) {
    return fetch('/attendance/punch', { method: 'POST', body: fd, credentials: 'same-origin' });
  }

  // Flushes the queue in order, stopping on the first failure so punches
  // stay in order and aren't skipped past a still-unreachable network.
  function flushQueue() {
    return queueAll().then(function (items) {
      if (!items.length) return;
      var chain = Promise.resolve();
      items.forEach(function (item) {
        chain = chain.then(function () {
          return postPunch(buildFormData(item))
            .then(function (resp) {
              if (resp.ok) return queueRemove(item.id);
              // Server rejected it (e.g. validation error) — drop it rather
              // than retry forever; the employee already had a chance to
              // fix it live if they were online at submit time.
              return queueRemove(item.id);
            })
            .catch(function () {
              // Still offline — leave it queued, stop the chain here.
              return Promise.reject('offline');
            });
        });
      });
      return chain.catch(function () { /* stop flushing, retry later */ });
    });
  }

  window.OmniAttendance = {
    queuePunch: function (lat, lng, reason, photoBlob, photoName) {
      return queueAdd({ lat: lat, lng: lng, reason: reason, photoBlob: photoBlob, photoName: photoName, ts: Date.now() });
    },
    submitOrQueue: function (lat, lng, reason, photoBlob, photoName) {
      if (!navigator.onLine) {
        return this.queuePunch(lat, lng, reason, photoBlob, photoName).then(function () {
          return { queued: true };
        });
      }
      return postPunch(buildFormData({ lat: lat, lng: lng, reason: reason, photoBlob: photoBlob, photoName: photoName }))
        .then(function (resp) {
          if (resp.ok) return resp.json().then(function (data) { return { queued: false, data: data }; });
          return resp.text().then(function (msg) { throw new Error(msg); });
        })
        .catch(function (err) {
          // fetch itself failed (network error, not an HTTP error response) — queue it.
          if (err instanceof TypeError) {
            return window.OmniAttendance.queuePunch(lat, lng, reason, photoBlob, photoName).then(function () {
              return { queued: true };
            });
          }
          throw err;
        });
    },
    flushQueue: flushQueue,
    pendingCount: function () { return queueAll().then(function (items) { return items.length; }); },
  };

  window.addEventListener('online', flushQueue);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') flushQueue();
  });
  flushQueue();
})();
