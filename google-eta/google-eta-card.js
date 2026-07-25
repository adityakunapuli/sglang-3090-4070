/**
 * google-eta-card — Custom Lovelace card for ETA + route overlay.
 *
 * Shows person location markers on a Leaflet dark map.
 * Reads sensor entities from HA integration.
 */

(function () {
  if (!document.querySelector('link[data-leaflet-css]')) {
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    link.setAttribute('data-leaflet-css', 'true');
    document.head.appendChild(link);
  }
})();

function haversine(lat1, lon1, lat2, lon2) {
  var R = 6371000;
  var dlat = (lat2 - lat1) * Math.PI / 180;
  var dlon = (lon2 - lon1) * Math.PI / 180;
  var a = Math.sin(dlat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dlon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function decodePolyline(encoded) {
  var points = [];
  var index = 0, lat = 0, lng = 0;
  while (index < encoded.length) {
    var b, shift = 0, result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    var dlat = (result & 1) ? ~(result >> 1) : (result >> 1);
    lat += dlat;
    shift = 0; result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    var dlng = (result & 1) ? ~(result >> 1) : (result >> 1);
    lng += dlng;
    points.push([lat * 1e-5, lng * 1e-5]);
  }
  return points;
}

var GoogleEtaCard = class GoogleEtaCard extends HTMLElement {
  constructor() {
    super();
    this._map = null;
    this._routeLayer = null;
    this._personMarkers = {};
    this._currentRoute = null;
    this._config = {};
    this._hass = null;
    this._container = null;
    this._pendingMapEl = null;
    this._waitingLeaflet = false;
    this._sensorA = 'sensor.eta_to_wife';
    this._sensorB = 'sensor.eta_to_me';
    this._mapHeight = 400;
    this._nameA = 'Adi';
    this._nameB = 'Wife';
    this._latestData = null;
  }

  setConfig(config) {
    this._config = config;
    this._mapHeight = config.height || this._mapHeight;
    if (config.name_a) this._nameA = config.name_a.charAt(0).toUpperCase() + config.name_a.slice(1);
    if (config.name_b) this._nameB = config.name_b.charAt(0).toUpperCase() + config.name_b.slice(1);
  }

  getCardSize() {
    return 3;
  }

  connectedCallback() {
    this._initDOM();
    this._loadLeafletAndInit();
  }

  _initDOM() {
    this.innerHTML = '';
    this._container = document.createElement('div');
    this._container.style.cssText = 'display:flex;flex-direction:column;border-radius:16px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#1a1a2e;width:100%;';

    var mapEl = document.createElement('div');
    mapEl.id = 'eta-map';
    mapEl.style.cssText = 'flex:1;width:100%;min-height:0;';

    this._statusEl = document.createElement('div');
    this._statusEl.style.cssText = 'background:#16213e;padding:12px 16px;color:#e0e0e0;font-size:14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;border-top:1px solid rgba(255,255,255,0.08);flex-shrink:0;';

    this._container.appendChild(mapEl);
    this._container.appendChild(this._statusEl);
    this.appendChild(this._container);
    this._pendingMapEl = mapEl;
  }

  _loadLeafletAndInit() {
    if (this._map) return;

    if (typeof window.L !== 'undefined') {
      this._initMap(this._pendingMapEl);
      return;
    }

    if (this._waitingLeaflet) return;
    this._waitingLeaflet = true;

    var self = this;

    var script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.setAttribute('data-leaflet-js', 'true');
    script.onload = function () {
      self._waitingLeaflet = false;
      self._initMap(self._pendingMapEl);
    };
    script.onerror = function () {
      self._waitingLeaflet = false;
      self._statusEl.innerHTML = '<div style="color:#f87171;">Failed to load map library</div>';
    };
    document.head.appendChild(script);
  }

  _initMap(mapEl) {
    if (this._map) return;
    this._map = L.map(mapEl, {
      center: [0, 0],
      zoom: 5,
      zoomControl: true,
    });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(this._map);

    this._routeLayer = L.layerGroup().addTo(this._map);
    this._pendingMapEl = null;

    if (this._hass) {
      setTimeout(() => this._render(), 300);
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass) return;

    var sensorA = this._hass.states[this._sensorA];
    var sensorB = this._hass.states[this._sensorB];

    if (!sensorA && !sensorB) {
      this._statusEl.innerHTML = '<div style="opacity:0.5;">Loading sensor data...</div>';
      return;
    }

    var attrsA = sensorA ? sensorA.attributes : {};
    var attrsB = sensorB ? sensorB.attributes : {};

    var source = attrsA.source || 'idle';
    var distM = attrsA.distance_m || 0;
    var personA = attrsA.person_a || this._nameA;
    var personB = attrsA.person_b || this._nameB;
    var coordsA = attrsA.person_a_coords || [];
    var coordsB = attrsA.person_b_coords || [];

    this._drawMarkers(coordsA[0], coordsA[1], coordsB[0], coordsB[1], personA, personB);

    if (source === 'idle' || source === undefined) {
      var msg = distM > 0 ? distM + ' m apart' : 'at home';
      this._statusEl.innerHTML =
        '<div style="display:flex;align-items:center;gap:8px;">' +
        '<span style="color:#4ADE80;font-size:18px;">&#10003;</span>' +
        '<span style="font-weight:600;">At home</span></div>' +
        '<div style="font-size:13px;opacity:0.6;">' + msg + '</div>';
      this._latestData = null;
      this._routeLayer.clearLayers();
      return;
    }

    var aToWifeDur = attrsA.duration_text || '?? min';
    var aToWifeDist = attrsA.distance_text || '??';
    var bToAdiDur = attrsB.duration_text || '?? min';
    var bToAdiDist = attrsB.distance_text || '??';

    var miApart = (distM * 0.000621371).toFixed(1);

    var aToWifeDir = attrsA.direction || null;
    var bToAdiDir = attrsB.direction || null;

    this._latestData = {
      aToWife: { dir: aToWifeDir, latA: coordsA[0], lonA: coordsA[1], latB: coordsB[0], lonB: coordsB[1] },
      bToAdi: { dir: bToAdiDir, latA: coordsB[0], lonA: coordsB[1], latB: coordsA[0], lonB: coordsA[1] },
      coordsA: coordsA,
      coordsB: coordsB,
      nameA: personA,
      nameB: personB,
    };

    var html =
      '<div style="display:flex;flex-direction:column;gap:4px;">' +
      '<div style="font-weight:600;font-size:15px;">' +
      '<span style="color:#4285F4;">' + personA + '</span>' +
      '<span style="color:#888;"> → </span>' +
      '<span style="color:#EA4335;">' + personB + '</span></div>' +
      '<div style="font-size:13px;opacity:0.8;">' + miApart + ' mi apart · <span style="color:#AACCFF;">' + source + '</span></div>' +
      '</div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;">';

    if (aToWifeDir && aToWifeDir.polyline) {
      html += '<button class="route-btn" data-dir="adi_to_wife">→ ' + personB + ' (' + aToWifeDur + ', ' + aToWifeDist + ')</button>';
    }
    if (bToAdiDir && bToAdiDir.polyline) {
      html += '<button class="route-btn" data-dir="wife_to_adi">→ ' + personA + ' (' + bToAdiDur + ', ' + bToAdiDist + ')</button>';
    }
    html += '<button class="clear-btn">Clear</button>';
    html += '</div>';

    this._statusEl.innerHTML = html;

    var self = this;
    this._statusEl.querySelectorAll('.route-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var dir = btn.getAttribute('data-dir');
        if (dir === 'adi_to_wife') {
          self._loadRoute('adi_to_wife');
        } else {
          self._loadRoute('wife_to_adi');
        }
      });
    });
    this._statusEl.querySelectorAll('.clear-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { self._clearRoute(); });
    });
  }

  _drawMarkers(latA, lonA, latB, lonB, nameA, nameB) {
    if (!this._map) return;
    if (latA == null || latB == null) return;

    var self = this;
    Object.keys(this._personMarkers).forEach(function (key) {
      if (self._map.hasLayer(self._personMarkers[key])) {
        self._map.removeLayer(self._personMarkers[key]);
      }
    });
    this._personMarkers = {};

    var midLat = (latA + latB) / 2;
    var midLon = (lonA + lonB) / 2;
    this._map.setView([midLat, midLon], 11);

    var iconA = L.divIcon({
      className: 'person-marker',
      html: '<div style="width:24px;height:24px;border-radius:50%;background:#4285F4;border:2px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;font-size:11px;color:white;font-weight:600;cursor:pointer;">' + nameA.charAt(0) + '</div>',
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });
    var markerA = L.marker([latA, lonA], { icon: iconA }).addTo(this._map);
    markerA.on('click', function () {
      window.open('https://www.google.com/maps/dir/?api=1&destination=' + latA.toFixed(6) + ',' + lonA.toFixed(6), '_blank');
    });
    this._personMarkers.adi = markerA;

    var iconB = L.divIcon({
      className: 'person-marker',
      html: '<div style="width:24px;height:24px;border-radius:50%;background:#EA4335;border:2px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;font-size:11px;color:white;font-weight:600;cursor:pointer;">' + nameB.charAt(0) + '</div>',
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });
    var markerB = L.marker([latB, lonB], { icon: iconB }).addTo(this._map);
    markerB.on('click', function () {
      window.open('https://www.google.com/maps/dir/?api=1&destination=' + latB.toFixed(6) + ',' + lonB.toFixed(6), '_blank');
    });
    this._personMarkers.wife = markerB;
  }

  _loadRoute(dir) {
    if (!this._map || !this._routeLayer || !this._latestData) return;
    this._routeLayer.clearLayers();

    var routeData = dir === 'adi_to_wife' ? this._latestData.aToWife : this._latestData.bToAdi;
    if (!routeData.dir || !routeData.dir.polyline) return;

    var color = dir === 'adi_to_wife' ? '#4285F4' : '#EA4335';
    var label = dir === 'adi_to_wife'
      ? this._latestData.nameA + ' → ' + this._latestData.nameB
      : this._latestData.nameB + ' → ' + this._latestData.nameA;

    var points = decodePolyline(routeData.dir.polyline);
    if (points.length === 0) return;

    var routeLine = L.polyline(points, { color: color, weight: 4, opacity: 0.85 }).addTo(this._routeLayer);
    this._map.fitBounds(routeLine.getBounds(), { padding: [40, 40] });
    this._currentRoute = { dir: dir, color: color };

    var durationText = routeData.dir.duration_text || '';
    var distanceText = routeData.dir.distance_text || '';

    var otherDir = dir === 'adi_to_wife' ? 'wife_to_adi' : 'adi_to_wife';
    var otherBtn = '';
    if (otherDir === 'wife_to_adi' && this._latestData.bToAdi.dir && this._latestData.bToAdi.dir.polyline) {
      otherBtn = '<button class="route-btn" data-dir="wife_to_adi">→ ' + this._latestData.nameA + '</button>';
    } else if (otherDir === 'adi_to_wife' && this._latestData.aToWife.dir && this._latestData.aToWife.dir.polyline) {
      otherBtn = '<button class="route-btn" data-dir="adi_to_wife">→ ' + this._latestData.nameB + '</button>';
    }

    var self = this;
    this._statusEl.innerHTML =
      '<div style="font-weight:600;">' +
      'Route: <span style="color:' + color + '">' + label + '</span>' +
      (durationText ? ' · ' + durationText : '') +
      (distanceText ? ' · ' + distanceText : '') +
      '</div>' +
      '<div style="display:flex;gap:6px;margin-top:4px;">' +
      otherBtn +
      '<button class="clear-btn">Clear</button>' +
      '<a href="https://www.google.com/maps/dir/?api=1&origin=' + routeData.latA + ',' + routeData.lonA + '&destination=' + routeData.latB + ',' + routeData.lonB +
      '" target="_blank" style="color:#AACCFF;text-decoration:none;padding:4px 8px;">Google Maps ↗</a>' +
      '</div>';

    this._statusEl.querySelectorAll('.route-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        self._loadRoute(btn.getAttribute('data-dir'));
      });
    });
    this._statusEl.querySelectorAll('.clear-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { self._clearRoute(); });
    });
  }

  _clearRoute() {
    this._routeLayer.clearLayers();
    this._currentRoute = null;
    this._render();
  }

  disconnectedCallback() {
    if (this._map) {
      this._map.remove();
      this._map = null;
    }
  }
};

if (!customElements.get('google-eta-card')) {
  customElements.define('google-eta-card', GoogleEtaCard);
}
