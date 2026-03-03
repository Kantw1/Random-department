from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Tuple, Dict, Any
import random
import math
import datetime as dt
import json
import re
import unicodedata

import requests

from . import departments

app = FastAPI(title="Random French Department Picker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Constraints(BaseModel):
    start_location: Optional[str] = None
    min_distance_km: Optional[float] = None
    max_distance_km: Optional[float] = None
    start_date: Optional[str] = None  # ISO date
    end_date: Optional[str] = None  # ISO date
    require_good_weather: bool = False
    require_loto: bool = False
    require_water: bool = False
    require_camping: bool = False


class CampingInfo(BaseModel):
    name: str
    lat: float
    lon: float


class LotoInfo(BaseModel):
    label: str
    url: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    place: Optional[str] = None
    date: Optional[str] = None


class DepartmentResult(BaseModel):
    code: str
    name: str
    region: str
    lat: float
    lon: float
    reasons: List[str]
    campings: List[CampingInfo]
    lotos: List[LotoInfo]
    # Contour approximatif du département, sous forme de liste de [lat, lon]
    boundary: List[List[float]] = []


# Simple in‑memory caches to avoid hammering external APIs
_geocode_cache: Dict[str, Tuple[float, float]] = {}
_water_cache: Dict[str, bool] = {}
_camping_cache: Dict[str, List[Dict[str, Any]]] = {}
_weather_cache: Dict[Tuple[str, str, str], bool] = {}
_boundary_cache: Dict[str, List[List[float]]] = {}
_dept_geojson_index: Dict[str, List[List[float]]] = {}
_loto_raw_cache: Dict[str, List[Dict[str, Any]]] = {}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance great‑circle en kilomètres."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(
        dlambda / 2
    ) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def geocode_location(query: str) -> Optional[Tuple[float, float]]:
    if not query:
        return None
    key = query.strip().lower()
    if key in _geocode_cache:
        return _geocode_cache[key]

    url = "https://nominatim.openstreetmap.org/search"
    params = {"format": "json", "q": query, "limit": 1}
    headers = {"User-Agent": "RandomDepartmentApp/1.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    _geocode_cache[key] = (lat, lon)
    return lat, lon


def check_good_weather(
    dep_code: str,
    lat: float,
    lon: float,
    start_date: Optional[str],
    end_date: Optional[str],
) -> bool:
    """Retourne True si la météo semble correcte (peu de pluie, températures modérées)."""
    if not start_date and not end_date:
        # Pas de dates → aucune contrainte
        return True

    if not start_date:
        start_date = end_date
    if not end_date:
        end_date = start_date

    key = (dep_code, start_date, end_date)
    if key in _weather_cache:
        return _weather_cache[key]

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_probability_max,temperature_2m_max",
        "timezone": "Europe/Paris",
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily") or {}
        probs = daily.get("precipitation_probability_max") or []
        temps = daily.get("temperature_2m_max") or []
        if not probs or not temps:
            _weather_cache[key] = True  # pas de données, on ne bloque pas
            return True

        avg_prob = sum(probs) / len(probs)
        avg_temp = sum(temps) / len(temps)

        # Heuristique simple : pas trop de pluie et températures entre 15°C et 30°C
        ok = avg_prob <= 50 and 15 <= avg_temp <= 30
        _weather_cache[key] = ok
        return ok
    except Exception:
        # En cas d'erreur API, on ne bloque pas le département
        _weather_cache[key] = True
        return True


def _overpass_has_feature(
    dep_code: str, lat: float, lon: float, radius_km: float, overpass_filter: str
) -> bool:
    radius_m = int(radius_km * 1000)
    query = f"""
    [out:json][timeout:25];
    (
      node{overpass_filter}(around:{radius_m},{lat},{lon});
      way{overpass_filter}(around:{radius_m},{lat},{lon});
      relation{overpass_filter}(around:{radius_m},{lat},{lon});
    );
    out 1;
    """
    url = "https://overpass-api.de/api/interpreter"
    try:
        resp = requests.post(url, data=query.encode("utf-8"), timeout=25)
        resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])
        return len(elements) > 0
    except Exception:
        # On ne bloque pas si Overpass échoue
        return True


def _overpass_get_features(
    lat: float, lon: float, radius_km: float, overpass_filter: str
) -> List[Dict[str, Any]]:
    """Retourne une liste brute d'éléments Overpass (avec lat/lon si possible)."""
    radius_m = int(radius_km * 1000)
    query = f"""
    [out:json][timeout:25];
    (
      node{overpass_filter}(around:{radius_m},{lat},{lon});
      way{overpass_filter}(around:{radius_m},{lat},{lon});
      relation{overpass_filter}(around:{radius_m},{lat},{lon});
    );
    out center 50;
    """
    url = "https://overpass-api.de/api/interpreter"
    try:
        resp = requests.post(url, data=query.encode("utf-8"), timeout=25)
        resp.raise_for_status()
        data = resp.json()
        return data.get("elements", []) or []
    except Exception:
        return []


def has_water_nearby(dep_code: str, lat: float, lon: float) -> bool:
    if dep_code in _water_cache:
        return _water_cache[dep_code]
    # Rivières / lacs / plans d'eau dans un rayon de 30 km
    overpass_filter = '["natural"="water"]["water"!="reservoir"];way["waterway"="river"];'
    ok = _overpass_has_feature(dep_code, lat, lon, 30.0, overpass_filter)
    _water_cache[dep_code] = ok
    return ok


def has_camping_nearby(dep_code: str, lat: float, lon: float) -> bool:
    campings = get_campings_nearby(dep_code, lat, lon)
    return len(campings) > 0


def get_campings_nearby(dep_code: str, lat: float, lon: float) -> List[Dict[str, Any]]:
    """Retourne la liste des campings proches (nom + coordonnées)."""
    if dep_code in _camping_cache:
        return _camping_cache[dep_code]

    overpass_filter = '["tourism"="camp_site"]'
    elements = _overpass_get_features(lat, lon, 30.0, overpass_filter)
    campings: List[Dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name") or "Camping sans nom"
        if el.get("type") == "node":
            el_lat = el.get("lat")
            el_lon = el.get("lon")
        else:
            center = el.get("center") or {}
            el_lat = center.get("lat")
            el_lon = center.get("lon")
        if el_lat is None or el_lon is None:
            continue
        campings.append(
            {
                "name": name,
                "lat": float(el_lat),
                "lon": float(el_lon),
            }
        )

    _camping_cache[dep_code] = campings
    return campings


def _load_geojson_boundaries() -> None:
    """Charge le GeoJSON des départements et indexe les contours par code."""
    global _dept_geojson_index
    if _dept_geojson_index:
        return

    import json
    import os

    geojson_path = os.path.join(os.path.dirname(__file__), "data", "departements.geojson")
    try:
        with open(geojson_path, "r", encoding="utf-8") as f:
            gj = json.load(f)
    except FileNotFoundError:
        _dept_geojson_index = {}
        return

    features = gj.get("features") or []
    index: Dict[str, List[List[float]]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        code = props.get("code")
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if not code or not coords:
            continue

        # On simplifie en prenant uniquement l'anneau extérieur principal
        ring: List[List[float]] = []
        if gtype == "Polygon":
            ring = coords[0] if coords else []
        elif gtype == "MultiPolygon":
            ring = coords[0][0] if coords and coords[0] else []

        latlon: List[List[float]] = []
        for pt in ring:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            lon, lat = float(pt[0]), float(pt[1])
            latlon.append([lat, lon])

        if latlon:
            index[code] = latlon

    _dept_geojson_index = index


def get_department_boundary(dep_code: str) -> List[List[float]]:
    """
    Retourne le contour EXACT (issu d'un GeoJSON officiel open data)
    du département sous forme de liste [lat, lon].
    """
    if dep_code in _boundary_cache:
        return _boundary_cache[dep_code]

    _load_geojson_boundaries()
    boundary = _dept_geojson_index.get(dep_code, [])
    _boundary_cache[dep_code] = boundary
    return boundary


def _slugify_department_for_loto(name: str) -> str:
    """Transforme un nom de département en segment d'URL pour agenda-loto.net."""
    norm = unicodedata.normalize("NFD", name)
    norm = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    norm = norm.replace(" ", "-")
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
    return "".join(c for c in norm if c in allowed)


def _parse_loto_events_from_html(html: str) -> List[Dict[str, Any]]:
    """Extrait les objets Event présents en JSON-LD dans la page agenda-loto.net."""
    events: List[Dict[str, Any]] = []
    for match in re.finditer(
        r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
        html,
        flags=re.DOTALL,
    ):
        block = match.group(1)
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        def collect(obj: Any) -> None:
            if isinstance(obj, dict):
                t = obj.get("@type")
                if t == "Event" or (isinstance(t, list) and "Event" in t):
                    events.append(obj)
                for v in obj.values():
                    collect(v)
            elif isinstance(obj, list):
                for v in obj:
                    collect(v)

        collect(data)
    return events


def _load_all_lotos_for_department(dep_name: str, dep_code: str) -> List[Dict[str, Any]]:
    """Charge depuis agenda-loto.net la liste brute des lotos d'un département (sans filtrage de dates)."""
    if dep_code in _loto_raw_cache:
        return _loto_raw_cache[dep_code]

    slug = _slugify_department_for_loto(dep_name)
    url = f"https://agenda-loto.net/evenements/{slug}"
    headers = {"User-Agent": "RandomDepartmentApp/1.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception:
        _loto_raw_cache[dep_code] = []
        return []

    events_ld = _parse_loto_events_from_html(resp.text)
    parsed: List[Dict[str, Any]] = []
    for ev in events_ld:
        try:
            name = ev.get("name") or "Loto"
            url_ev = ev.get("url") or url
            start_raw = ev.get("startDate")  # ex: "06/03/2026"
            start_iso: Optional[str] = None
            if isinstance(start_raw, str):
                try:
                    d = dt.datetime.strptime(start_raw, "%d/%m/%Y").date()
                    start_iso = d.isoformat()
                except ValueError:
                    start_iso = None

            loc = (ev.get("location") or {}).get("address") or {}
            place = loc.get("addressLocality")
            geo = (ev.get("location") or {}).get("geo") or {}
            lat = geo.get("latitude")
            lon = geo.get("longitude")

            parsed.append(
                {
                    "label": name,
                    "url": url_ev,
                    "lat": float(lat) if lat is not None else None,
                    "lon": float(lon) if lon is not None else None,
                    "place": place,
                    "date": start_iso,
                }
            )
        except Exception:
            continue

    _loto_raw_cache[dep_code] = parsed
    return parsed


def get_lotos_for_department(
    dep_name: str,
    dep_code: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Retourne les lotos d'un département sur agenda-loto.net,
    filtrés sur la période demandée (si fournie).
    """
    raw_events = _load_all_lotos_for_department(dep_name, dep_code)
    if not raw_events:
        return []

    start_d = dt.date.fromisoformat(start_date) if start_date else None
    end_d = dt.date.fromisoformat(end_date) if end_date else None

    results: List[Dict[str, Any]] = []
    for ev in raw_events:
        ev_date_iso = ev.get("date")
        if ev_date_iso and (start_d or end_d):
            try:
                ev_d = dt.date.fromisoformat(ev_date_iso)
            except ValueError:
                ev_d = None
            if ev_d:
                if start_d and ev_d < start_d:
                    continue
                if end_d and ev_d > end_d:
                    continue
        results.append(ev)

    return results


@app.get("/api/departments", response_model=List[DepartmentResult])
def list_departments() -> List[DepartmentResult]:
    """Return the static list of all departments (for debugging / UI help)."""
    return [
        DepartmentResult(
            code=d["code"],
            name=d["name"],
            region=d["region"],
            lat=d["lat"],
            lon=d["lon"],
            reasons=[],
            campings=[],
            lotos=[],
            boundary=[],
        )
        for d in departments.DEPARTMENTS
    ]


def _compute_matching_departments(constraints: Constraints) -> List[DepartmentResult]:
    """Retourne tous les départements qui respectent les contraintes."""
    # Gestion des dates
    start_date = constraints.start_date
    end_date = constraints.end_date
    if start_date and end_date and start_date > end_date:
        # On swap si les dates sont inversées
        start_date, end_date = end_date, start_date

    origin_lat = origin_lon = None
    if constraints.start_location and (
        constraints.max_distance_km is not None
        or constraints.min_distance_km is not None
    ):
        coords = geocode_location(constraints.start_location)
        if not coords:
            raise HTTPException(
                status_code=400,
                detail="Point de départ introuvable (géocodage).",
            )
        origin_lat, origin_lon = coords

    candidates: List[Dict[str, Any]] = []
    for d in departments.DEPARTMENTS:
        d_lat, d_lon = d["lat"], d["lon"]

        # Filtre sur distance
        if origin_lat is not None and origin_lon is not None:
            dist = haversine_km(origin_lat, origin_lon, d_lat, d_lon)
            if constraints.min_distance_km is not None and dist < constraints.min_distance_km:
                continue
            if constraints.max_distance_km is not None and dist > constraints.max_distance_km:
                continue

        candidates.append(d)

    random.shuffle(candidates)

    matches: List[DepartmentResult] = []

    for d in candidates:
        reasons: List[str] = ["Département compatible avec les contraintes."]
        code = d["code"]
        lat = d["lat"]
        lon = d["lon"]
        campings_list: List[Dict[str, Any]] = []
        lotos_list: List[Dict[str, Any]] = []

        # Météo
        if constraints.require_good_weather:
            if not check_good_weather(code, lat, lon, start_date, end_date):
                continue
            reasons.append("Météo jugée agréable sur la période demandée (Open‑Meteo).")

        # Eau à proximité
        if constraints.require_water:
            if not has_water_nearby(code, lat, lon):
                continue
            reasons.append("Présence probable de rivière(s) ou lac(s) à proximité (données OpenStreetMap).")

        # Camping à proximité
        if constraints.require_camping:
            campings_list = get_campings_nearby(code, lat, lon)
            if not campings_list:
                continue
            reasons.append(
                f"{len(campings_list)} camping(s) trouvé(s) dans le secteur (données OpenStreetMap)."
            )

        # Loto (agenda-loto.net) – prise en charge « douce » : on ne bloque pas,
        # et on exige au moins un événement dans la période si elle est cochée.
        if constraints.require_loto:
            lotos_list = get_lotos_for_department(d["name"], code, start_date, end_date)
            if not lotos_list:
                # Aucune date de loto trouvée sur la période → on essaie un autre département
                continue
            reasons.append(
                f"{len(lotos_list)} loto(s) trouvé(s) dans le département sur agenda-loto.net."
            )

        boundary = get_department_boundary(code)

        matches.append(
            DepartmentResult(
                code=d["code"],
                name=d["name"],
                region=d["region"],
                lat=d["lat"],
                lon=d["lon"],
                reasons=reasons,
                campings=[CampingInfo(**c) for c in campings_list],
                lotos=[LotoInfo(**l) for l in lotos_list],
                boundary=boundary,
            )
        )

    return matches


@app.post("/api/random-department", response_model=DepartmentResult)
def pick_random_department(constraints: Constraints) -> DepartmentResult:
    """
    Pick a random French department that matches the given constraints.
    """
    matches = _compute_matching_departments(constraints)
    if not matches:
        raise HTTPException(
            status_code=404,
            detail="Aucun département ne correspond à ces contraintes.",
        )
    return random.choice(matches)


@app.post("/api/matching-departments", response_model=List[DepartmentResult])
def list_matching_departments(constraints: Constraints) -> List[DepartmentResult]:
    """
    Retourne la liste complète des départements qui respectent les contraintes.
    """
    return _compute_matching_departments(constraints)


