# Medienverwaltung

Eine webbasierte Anwendung zur Verwaltung Ihrer physischen Mediensammlung (CDs, Vinyl, Bücher, Filme, Videospiele). 
Das System bietet Integrationen zu Discogs, Spotify und Google Books, um Metadaten und Cover automatisch zu laden.

## Funktionen

*   **Inventarisierung**: Erfassen von Medien mit Barcode-Scanner Unterstützung oder manueller Eingabe.
*   **Automatische Metadaten**:
    *   **Musik**: Suche via Discogs API (Cover, Tracklisten, Jahr).
    *   **Bücher**: Suche via Google Books, OpenLibrary und Amazon (Cover, Autor, Beschreibung).
*   **Spotify Integration**: Vorschau-Player für CDs und Vinyls direkt in der Detailansicht.
*   **Standort-Verwaltung**: Hierarchische Lagerorte (z.B. Wohnzimmer > Regal A > Fach 1).
*   **Verleih-Status**: Markieren von verliehenen Gegenständen.
*   **QR-Codes**: Generierung von QR-Codes für jedes Item zum schnellen Scannen.
*   **Backup & Restore**: Vollständige Sicherung der Datenbank und Bilder als ZIP-Datei.
*   **Benutzerverwaltung**: Rollenbasierter Zugriff (Admin/User).

## Installation mit Docker (Empfohlen)

Die Anwendung ist für den Betrieb mit Docker Compose optimiert.

### 1. Vorbereitung

Stellen Sie sicher, dass Docker und Docker Compose installiert sind.
Erstellen Sie eine `docker-compose.yml` (oder nutzen Sie die beiliegende) und starten Sie den Container.

### 2. Konfiguration (Environment Variablen)

Die Konfiguration erfolgt über Umgebungsvariablen in der `docker-compose.yml` oder einer `.env` Datei.

| Variable | Standard | Beschreibung |
| :--- | :--- | :--- |
| `APP_PORT` | `5000` | Der Port, auf dem die Web-Oberfläche erreichbar ist (Host-Port). |
| `UPLOAD_PATH` | `./data/uploads` | Lokaler Pfad für hochgeladene Bilder (Cover). |
| `DB_PATH` | `./data/instance` | Lokaler Pfad für die SQLite Datenbank. |
| `SECRET_KEY` | `dev-key...` | Sicherheitsschlüssel für Sessions (sollte geändert werden). |

**Beispiel `docker-compose.yml`:**

```yaml
version: '3.8'

services:
  web:
    build: .
    container_name: medienverwaltung
    ports:
      # Format: "HOST_PORT:CONTAINER_PORT"
      # Ändern Sie den ersten Wert, um den Port anzupassen (z.B. "8080:5000")
      - "${APP_PORT:-5000}:5000"
    
    volumes:
      # Persistente Datenhaltung
      - ${UPLOAD_PATH:-./data/uploads}:/app/static/uploads
      - ${DB_PATH:-./data/instance}:/app/instance
    
    restart: unless-stopped
```

### 3. Starten

```bash
docker-compose up -d
```

Die Anwendung ist nun unter `http://localhost:5000` (oder dem konfigurierten Port) erreichbar.

## Erster Login

Beim ersten Start wird automatisch ein Administrator-Konto angelegt.

*   **Benutzername:** `admin`
*   **Passwort:** `admin123`

> **Wichtig:** Bitte ändern Sie das Passwort sofort nach dem ersten Login unter "Profil".

## Einrichtung externer Dienste

Um alle Funktionen nutzen zu können, sollten API-Schlüssel in den **Einstellungen** hinterlegt werden:

1.  **Discogs (für Musik-Metadaten):**
    *   Erstellen Sie einen Account auf Discogs.
    *   Generieren Sie einen "Personal Access Token" unter Developer Settings.
    *   Tragen Sie diesen in den Einstellungen ein.

2.  **Spotify (für Player-Integration):**
    *   Erstellen Sie eine App im Spotify Developer Dashboard.
    *   Kopieren Sie "Client ID" und "Client Secret".
    *   Tragen Sie diese in den Einstellungen ein.

## Backup

Im Admin-Bereich können Sie jederzeit ein vollständiges Backup herunterladen. Dieses enthält die SQLite-Datenbank sowie alle Bilder. Zum Wiederherstellen laden Sie die ZIP-Datei einfach wieder hoch.
