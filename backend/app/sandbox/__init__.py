"""Code that runs inside the renderer container: Chromium lays out slides and (later) records
video there. That container holds no database or Redis credentials and has no route to the
internet or to the other services except the render worker, so a page that misbehaves in
Chromium has nothing to read and nowhere to send it. Nothing here imports app.config."""
