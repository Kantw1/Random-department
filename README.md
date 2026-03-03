## Random-department

Application de tirage au sort d'un département français avec contraintes
(météo, loto, eau, camping, distance).

### Lancer le backend (FastAPI)

Depuis la racine du projet :

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Le backend écoute alors sur `http://127.0.0.1:8000`.

### Lancer le frontend

Le plus simple est d'ouvrir directement `frontend/index.html` dans votre
navigateur (double‑clic depuis votre explorateur de fichiers) ou de servir le
dossier `frontend` avec un petit serveur statique :

```bash
cd frontend
python -m http.server 4173
```

Puis aller sur `http://localhost:4173`.

### Limitations actuelles

- Les contraintes (météo, loto agenda-loto.net, rivières/lacs, campings,
  distance depuis un point de départ) sont déjà prévues dans l'API mais pas
  encore entièrement implémentées. Actuellement, le département est tiré au
  sort parmi une liste statique.
