const form = document.getElementById("constraints-form");
const statusEl = document.getElementById("status");
const resultSection = document.getElementById("result");
const deptNameEl = document.getElementById("dept-name");
const deptCodeEl = document.getElementById("dept-code");
const deptRegionEl = document.getElementById("dept-region");
const reasonsEl = document.getElementById("reasons");
const campingsBlock = document.getElementById("campings-block");
const campingsEl = document.getElementById("campings");
const lotosBlock = document.getElementById("lotos-block");
const lotosEl = document.getElementById("lotos");

let mapInstance = null;
let mapMarker = null;
let campingsLayer = null;
let lotosLayer = null;
let boundaryLayer = null;

async function callApi(payload) {
  let baseUrl;
  if (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1" ||
    window.location.origin.startsWith("file:")
  ) {
    baseUrl = "http://127.0.0.1:8000";
  } else {
    baseUrl = window.location.origin;
  }

  const res = await fetch(`${baseUrl}/api/random-department`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Erreur serveur");
  }

  return res.json();
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  statusEl.textContent = "Tirage en cours...";
  statusEl.classList.remove("error");
  resultSection.classList.add("hidden");
  campingsBlock.classList.add("hidden");
  lotosBlock.classList.add("hidden");
  campingsEl.innerHTML = "";
  lotosEl.innerHTML = "";

  const payload = {
    start_location: document.getElementById("startLocation").value || null,
    max_distance_km: document.getElementById("maxDistance").value
      ? Number(document.getElementById("maxDistance").value)
      : null,
    start_date: document.getElementById("startDate").value || null,
    end_date: document.getElementById("endDate").value || null,
    require_good_weather: document.getElementById("goodWeather").checked,
    require_loto: document.getElementById("needLoto").checked,
    require_water: document.getElementById("needWater").checked,
    require_camping: document.getElementById("needCamping").checked,
  };

  try {
    const result = await callApi(payload);
    statusEl.textContent = "";

    deptNameEl.textContent = result.name;
    deptCodeEl.textContent = result.code;
    deptRegionEl.textContent = `Région : ${result.region}`;

    reasonsEl.innerHTML = "";
    (result.reasons || []).forEach((reason) => {
      const li = document.createElement("li");
      li.textContent = reason;
      reasonsEl.appendChild(li);
    });

    // Liste des campings (si demandés côté backend)
    if (Array.isArray(result.campings) && result.campings.length > 0) {
      campingsEl.innerHTML = "";
      result.campings.forEach((camp) => {
        const li = document.createElement("li");
        const coord =
          typeof camp.lat === "number" && typeof camp.lon === "number"
            ? ` (${camp.lat.toFixed(3)}, ${camp.lon.toFixed(3)})`
            : "";
        li.textContent = `${camp.name}${coord}`;
        campingsEl.appendChild(li);
      });
      campingsBlock.classList.remove("hidden");
    } else {
      campingsBlock.classList.add("hidden");
    }

    // Liens vers les lotos (recherche agenda-loto.net)
    if (Array.isArray(result.lotos) && result.lotos.length > 0) {
      lotosEl.innerHTML = "";
      result.lotos.forEach((loto) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = loto.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = loto.label;
        li.appendChild(a);
        lotosEl.appendChild(li);
      });
      lotosBlock.classList.remove("hidden");
    } else {
      lotosBlock.classList.add("hidden");
    }

    if (window.L) {
      const coords = [result.lat, result.lon];
      if (!mapInstance) {
        mapInstance = L.map("map").setView(coords, 7);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "© OpenStreetMap contributeurs",
          maxZoom: 18,
        }).addTo(mapInstance);
      } else {
        mapInstance.setView(coords, 7);
      }

      // Marqueur principal sur le centre du département
      if (mapMarker) {
        mapMarker.setLatLng(coords);
      } else {
        mapMarker = L.marker(coords).addTo(mapInstance);
      }

      // Contour du département
      if (boundaryLayer) {
        mapInstance.removeLayer(boundaryLayer);
        boundaryLayer = null;
      }
      if (Array.isArray(result.boundary) && result.boundary.length > 0) {
        const latlngs = result.boundary.map((pair) =>
          Array.isArray(pair) && pair.length >= 2 ? [pair[0], pair[1]] : null
        ).filter(Boolean);
        if (latlngs.length > 0) {
          boundaryLayer = L.polygon(latlngs, {
            color: "#22c55e",
            weight: 2,
            fillColor: "#22c55e",
            fillOpacity: 0.08,
          }).addTo(mapInstance);
        }
      }

      // Marqueurs des campings
      if (campingsLayer) {
        mapInstance.removeLayer(campingsLayer);
        campingsLayer = null;
      }
      if (Array.isArray(result.campings) && result.campings.length > 0) {
        const campingMarkers = result.campings
          .filter(
            (c) =>
              typeof c.lat === "number" &&
              typeof c.lon === "number" &&
              !Number.isNaN(c.lat) &&
              !Number.isNaN(c.lon),
          )
          .map((c) =>
            L.marker([c.lat, c.lon]).bindPopup(
              `<strong>${c.name}</strong><br/>(${c.lat.toFixed(
                3,
              )}, ${c.lon.toFixed(3)})`,
            ),
          );
        if (campingMarkers.length > 0) {
          campingsLayer = L.layerGroup(campingMarkers).addTo(mapInstance);
        }
      }

      // Marqueurs pour les lotos
      if (lotosLayer) {
        mapInstance.removeLayer(lotosLayer);
        lotosLayer = null;
      }
      if (Array.isArray(result.lotos) && result.lotos.length > 0) {
        const lotoMarkers = result.lotos
          .filter(
            (l) =>
              typeof l.lat === "number" &&
              typeof l.lon === "number" &&
              !Number.isNaN(l.lat) &&
              !Number.isNaN(l.lon),
          )
          .map((l) =>
            L.circleMarker([l.lat, l.lon], {
              radius: 7,
              color: "#ef4444",
              weight: 2,
              fillColor: "#ef4444",
              fillOpacity: 0.9,
            }).bindPopup(
              `<strong>${l.label}</strong>${
                l.date ? `<br/>Date : ${l.date}` : ""
              }${
                l.place ? `<br/>Lieu : ${l.place}` : ""
              }<br/><a href="${l.url}" target="_blank" rel="noopener noreferrer">Voir la fiche</a>`,
            ),
          );

        if (lotoMarkers.length > 0) {
          lotosLayer = L.layerGroup(lotoMarkers).addTo(mapInstance);
        }
      }

      setTimeout(() => {
        mapInstance.invalidateSize();
      }, 150);
    }

    resultSection.classList.remove("hidden");
  } catch (err) {
    console.error(err);
    statusEl.textContent =
      "Impossible de tirer un département avec ces contraintes.";
    statusEl.classList.add("error");
  }
});

