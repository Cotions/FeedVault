Page shapes for checking the userscript without opening the sites: written
by hand (nothing scraped), only the parts the script reads (paths, post
links, images inside them). Served at the site's own path in a browser
whose every request is answered locally (Playwright `page.route`), they
stand in for the real pages; `frontend/scripts/userscript.test.js` reads
their links too. The ids are the fake downloaders' (backend/tests).
