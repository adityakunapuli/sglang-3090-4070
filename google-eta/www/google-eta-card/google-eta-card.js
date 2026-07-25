(function() {
  'use strict';

  // ================================================================
  // Polyline decoder — Google Directions API encoded polyline
  // ================================================================
  function decodePolyline(encoded) {
    if (!encoded || typeof encoded !== 'string') return [];
    var points = [], index = 0, lat = 0, lng = 0;
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

  // ================================================================
  // Haversine
  // ================================================================
  function haversine(lat1, lon1, lat2, lon2) {
    var R = 6371000;
    var a = Math.sin((lat2 - lat1) * Math.PI / 360) ** 2 +
      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
      Math.sin((lon2 - lon1) * Math.PI / 360) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function formatDistance(meters) {
    if (meters < 1000) return Math.round(meters) + ' m';
    return (meters / 1000).toFixed(1) + ' km';
  }

  function _parseJson(value) {
    if (typeof value === 'object') return value;
    try { return JSON.parse(value); } catch (e) { if (typeof value === 'string') return null; return value; }
  }

  // ================================================================
  // Custom Element — defined immediately so HA can find it
  // ================================================================
  class GoogleEtaCard extends HTMLElement {
    constructor() {
      super();
      this._config = {};
      this._hass = null;
      this._container = null;
      this._map = null;
      this._routeLayer = null;
      this._markerA = null;
      this._markerB = null;
      this._height = 400;
      this._personA = 'person.aditya_kunapuli';
      this._personB = 'person.mrs_wife';
      this._nameA = 'Adi';
      this._nameB = 'Wife';
      this._lastData = null;
      this._refreshInterval = null;
      this._waitingLeaflet = false;
    }

    setConfig(config) {
      this._config = config;
      this._height = config.height || 400 || this._height;
      this._personA = config.person_a || this._personA;
      this._personB = config.person_b || this._personB;
      this._nameA = config.name_a || this._nameA;
      this._nameB = config.name_b || this._nameB;
      this._subscribed = false;
    }

    getCardSize() { return 2 + Math.round(this._height / 120); }

    set hass(hass) {
      this._hass = hass;
      this._subscribe();
    }

    _subscribe() {
      if (this._subscribed || !this._hass || !this._hass.connection) return;
      var self = this;
      var conn = this._hass.connection;
      try {
        conn.subscribeEvents(function (evt) {
          if (!evt || !evt.data || !evt.data.entity_id) return;
          var eid = evt.data.entity_id;
          if (eid !== self._personA && eid !== self._personB &&
              eid !== 'input_text.eta_to_wife' && eid !== 'input_text.eta_to_me') return;
          self._updateFromHass();
        }, 'state_changed').then(function (unsub) {
          self._unsub = unsub;
          self._subscribed = true;
        });
      } catch (e) {
        if (this._refreshInterval) clearInterval(this._refreshInterval);
        this._refreshInterval = setInterval(function () { self._updateFromHass(); }, 5000);
        this._subscribed = true;
      }
      this._updateFromHass();
    }

    connectedCallback() {
      this.innerHTML = '';
      this._container = document.createElement('div');
      this._container.style.cssText =
        'display:flex;flex-direction:column;border-radius:16px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f172a;width:100%;';
      var mapEl = document.createElement('div');
      mapEl.id = 'gmap';
      mapEl.style.cssText = 'width:100%;height:' + this._height + 'px;flex-shrink:0;';
      this._panel = document.createElement('div');
      this._panel.style.cssText =
        'background:#1e293b;padding:12px 16px;color:#e2e8f0;font-size:13px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;border-top:1px solid #334155;flex-shrink:0;';
      this._container.appendChild(mapEl);
      this._container.appendChild(this._panel);
      this.appendChild(this._container);
      this._pendingMapEl = mapEl;
      this._tryInitMap();
    }

    disconnectedCallback() {
      if (this._refreshInterval) clearInterval(this._refreshInterval);
      if (this._unsub) { try { this._unsub(); } catch (e) {} this._unsub = null; }
      this._subscribed = false;
      if (this._map) { this._map.remove(); this._map = null; }
    }

    _tryInitMap() {
      if (this._map || !this._pendingMapEl) return;
      if (typeof window.L === 'undefined') {
        if (this._waitingLeaflet) return;
        this._waitingLeaflet = true;
        if (!document.querySelector('link[data-leaflet-css]')) {
          var css = document.createElement('link');
          css.rel = 'stylesheet';
          css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
          css.setAttribute('data-leaflet-css', 'true');
          document.head.appendChild(css);
        }
        if (typeof window.L === 'undefined') {
          var self = this;
          // Check every 200ms for 10s
          var check = setInterval(function () {
            if (typeof window.L !== 'undefined') {
              clearInterval(check);
              self._waitingLeaflet = false;
              self._tryInitMap();
            }
          }, 200);
          setTimeout(function () { clearInterval(check); self._waitingLeaflet = false; }, 10000);
        }
        return;
      }
      this._initMap(this._pendingMapEl);
    }

    _updateFromHass() {
      if (!this._hass || !this._map) return;
      var helperWife = this._hass.states['input_text.eta_to_wife'];
      var helperMe = this._hass.states['input_text.eta_to_me'];
      var helperWifeState = helperWife ? helperWife.state : '';
      var helperMeState = helperMe ? helperMe.state : '';
      var dataWife = helperWifeState ? _parseJson(helperWifeState) : null;
      var dataMe = helperMeState ? _parseJson(helperMeState) : null;
      if (dataWife || dataMe) {
        this._updateMarkersFromHelpers(dataWife, dataMe);
        this._handleData(dataWife, dataMe);
      } else {
        this._fallbackFromHA();
      }
    }

    _handleData(dataWife, dataMe) {
      var data = dataWife || dataMe;
      if (!data) {
        this._panel.innerHTML = '<span style="opacity:0.5;">Unable to load data</span>';
        return;
      }

      var personA = data.person_a || this._nameA;
      var personB = data.person_b || this._nameB;
      var hasWife = dataWife && dataWife.direction && dataWife.direction.polyline;
      var hasMe = dataMe && dataMe.direction && dataMe.direction.polyline;

      if (data.source === 'idle') {
        var distM = data.distance_m || 0;
        var msg = distM > 0 ? 'Together · ' + distM + ' m apart' : 'Together · 0 min';
        this._panel.innerHTML =
          '<div style="display:flex;align-items:center;gap:10px;">' +
            '<span style="color:#4ade80;font-size:18px;">&#9679;</span>' +
            '<div><div style="font-weight:600;">At home</div>' +
            '<div style="font-size:12px;opacity:0.6;">' + msg + '</div></div></div>';
        this._drawMarkersFromCoords(data);
        return;
      }

      var dir = data.direction || {};
      var durText = dir.duration_text || '';
      var distText = dir.distance_text || '';

      this._panel.innerHTML =
        '<div style="flex:1;min-width:180px;">' +
          '<div style="font-size:16px;font-weight:600;">' +
            '<span style="color:#60a5fa;">' + personA + '</span> ' +
            '<span style="color:#94a3b8;">&#8596;</span> ' +
            '<span style="color:#f87171;">' + personB + '</span>' +
          '</div><div style="font-size:12px;color:#94a3b8;">' + (durText + (distText ? ' ' + distText : '')) + '</div></div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap;">' +
          '<button class="route-btn" data-direction="adi_to_wife" ' + (hasWife ? 'data-polyline=' + JSON.stringify(dataWife.direction.polyline).replace(/"/g, '&quot;') + ' data-dur="' + (dataWife.direction.duration_text || '') + '" data-dist="' + (dataWife.direction.distance_text || '') + '"' : '') +
          ' style="background:#1d4ed8;color:white;padding:6px 14px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;">&#8594; Wife</button>' +
          '<button class="route-btn" data-direction="wife_to_adi" ' + (hasMe ? 'data-polyline=' + JSON.stringify(dataMe.direction.polyline).replace(/"/g, '&quot;') + ' data-dur="' + (dataMe.direction.duration_text || '') + '" data-dist="' + (dataMe.direction.distance_text || '') + '"' : '') +
          ' style="background:#b91c1c;color:white;padding:6px 14px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;">&#8592; Me</button>' +
          '<button class="clear-btn" data-action="reset" style="background:#334155;color:#e2e8f0;padding:6px 14px;border:none;border-radius:8px;cursor:pointer;font-size:13px;">Clear</button></div>';

      this._lastData = { wife: dataWife, me: dataMe, personA: personA, personB: personB };
      this._drawMarkersFromCoords(dataWife || dataMe);
      var self = this;
      this._panel.querySelectorAll('.route-btn').forEach(function (btn) {
        btn.addEventListener('click', function () { self._drawRouteFromButton(btn); });
      });
      this._panel.querySelectorAll('.clear-btn').forEach(function (btn) {
        btn.addEventListener('click', function () { self._clearRouteLine(); self._updateFromHass(); });
      });
    }

    _fallbackFromHA() {
      var deviceA = this._hass && this._hass.states['device_tracker.sm_s921u'];
      var deviceB = this._hass && this._hass.states['device_tracker.baby_mama'];
      if (!deviceA || !deviceB) return;
      var latA = parseFloat(deviceA.attributes.latitude);
      var lonA = parseFloat(deviceA.attributes.longitude);
      var latB = parseFloat(deviceB.attributes.latitude);
      var lonB = parseFloat(deviceB.attributes.longitude);
      if (isNaN(latA) || isNaN(lonA) || isNaN(latB) || isNaN(lonB)) return;
      var dist = haversine(latA, lonA, latB, lonB);
      this._drawMarkers(latA, lonA, latB, lonB, false);
      if (dist < 1000) {
        this._panel.innerHTML = '<div style="display:flex;align-items:center;gap:10px;"><span style="color:#60a5fa;font-size:18px;">&#9679;</span><div><div style="font-weight:600;">Close together</div><div style="font-size:12px;opacity:0.6;">' + formatDistance(dist) + ' apart</div></div></div>';
      } else {
        this._panel.innerHTML =
          '<div style="flex:1;"><div style="font-size:16px;font-weight:600;margin-bottom:2px;">' +
            '<span style="color:#60a5fa;">' + this._nameA + '</span> <span style="color:#94a3b8;">&#8596;</span> <span style="color:#f87171;">' + this._nameB + '</span></div>' +
          '<div style="font-size:12px;color:#94a3b8;">' + (dist / 1000).toFixed(1) + ' km · ' + (dist * 0.000621371).toFixed(1) + ' mi</div></div>' +
          '<div style="display:flex;gap:6px;">' +
          '<a target="_blank" style="background:#334155;color:#60a5fa;padding:6px 14px;border:none;border-radius:8px;cursor:pointer;text-decoration:none;font-size:13px;display:inline-block;" ' +
          'href="https://www.google.com/maps/dir/?api=1&origin=' + latA + ',' + lonA + '&destination=' + latB + ',' + lonB + '">Open Maps &#8599;</a></div>';
      }
    }

    _drawMarkersFromCoords(data) {
      if (!data) return;
      var latA, lonA, latB, lonB;
      if (data.lat_a !== undefined) {
        latA = data.lat_a; lonA = data.lon_a; latB = data.lat_b; lonB = data.lon_b;
      } else if (data.person_a_coords) {
        latA = data.person_a_coords[0]; lonA = data.person_a_coords[1];
        latB = data.person_b_coords[0]; lonB = data.person_b_coords[1];
      }
      if (!data || latA === undefined) return;
      var latValid = (latA >= -90 && latA <= 90 && latB >= -90 && latB <= 90);
      if (!latValid) return;
      this._drawMarkers(latA, lonA, latB, lonB, true);
    }

    _drawMarkers(latA, lonA, latB, lonB, centerOnMarkers) {
      var mid = [(latA + latB) / 2, (lonA + lonB) / 2];
      this._map.setView(mid, 12);
      if (this._markerA) this._map.removeLayer(this._markerA);
      if (this._markerB) this._map.removeLayer(this._markerB);
      var iconA = L.divIcon({className: 'eta-marker',
        html: '<div style="width:32px;height:32px;border-radius:50%;background:#60a5fa;border:3px solid #fff;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;box-shadow:0 2px 8px rgba(0,0,0,0.3);">A</div>',
        iconSize: [32, 32], iconAnchor: [16, 16]});
      var iconB = L.divIcon({className: 'eta-marker',
        html: '<div style="width:32px;height:32px;border-radius:50%;background:#f87171;border:3px solid #fff;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;box-shadow:0 2px 8px rgba(0,0,0,0.3);">B</div>',
        iconSize: [32, 32], iconAnchor: [16, 16]});
      this._markerA = L.marker([latA, lonA], {icon: iconA}).bindPopup(this._nameA).addTo(this._map);
      this._markerB = L.marker([latB, lonB], {icon: iconB}).bindPopup(this._nameB).addTo(this._map);
    }

    _clearRouteLine() { if (this._routeLayer) this._routeLayer.clearLayers(); }

    _drawRouteFromButton(btn) {
      var direction = btn.dataset.direction;
      if (!direction) return;
      this._clearRouteLine();
      var polyline = btn.dataset.polyline;
      if (!polyline) { this._drawRouteFromEntity(direction); return; }
      var points = decodePolyline(polyline);
      if (points.length === 0) return;
      var color = direction === 'adi_to_wife' ? '#60a5fa' : '#f87171';
      L.polyline(points, {color: color, weight: 4, opacity: 0.85}).addTo(this._routeLayer);
      try { var bnds = L.latLngBounds(points.map(function(p) { return [p[0], p[1]]; })); if (bnds.isValid()) this._map.fitBounds(bnds, {padding: [40, 40], maxZoom: 14}); } catch (e) {}
    }

    _drawRouteFromEntity(direction) {
      var entity = direction === 'adi_to_wife' ? 'input_text.eta_to_wife' : 'input_text.eta_to_me';
      var stateObj = this._hass && this._hass.states[entity];
      if (!stateObj || !stateObj.state) return;
      var parsed = _parseJson(stateObj.state);
      if (!parsed || parsed.source === 'idle' || !parsed.direction || !parsed.direction.polyline) return;
      var points = decodePolyline(parsed.direction.polyline);
      if (points.length === 0) return;
      var color = direction === 'adi_to_wife' ? '#60a5fa' : '#f87171';
      L.polyline(points, {color: color, weight: 4, opacity: 0.85}).addTo(this._routeLayer);
      try { var bnds = L.latLngBounds(points.map(function(p) { return [p[0], p[1]]; })); if (bnds.isValid()) this._map.fitBounds(bnds, {padding: [40, 40], maxZoom: 14}); } catch (e) {}
    }

    _initMap(mapEl) {
      if (this._map) return;
      this._map = L.map(mapEl, {zoomControl: true, attributionControl: false});
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {attribution: 'OSM CARTO', maxZoom: 19}).addTo(this._map);
      this._routeLayer = L.layerGroup().addTo(this._map);
      this._pendingMapEl = null;
      var self = this;
      setTimeout(function () { self._updateFromHass(); }, 500);
    }
  }

  // Register immediately — HA needs the element defined synchronously
  if (!customElements.get('google-eta-card')) {
    customElements.define('google-eta-card', GoogleEtaCard);
  }
})();
