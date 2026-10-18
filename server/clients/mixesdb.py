from clients.base import APIClient


class MixesDBClient(APIClient):
    base_url = "https://www.mixesdb.com/w/api.php"
    min_interval = 1.0

    def search(self, query, limit=10):
        """Full-text search: matches anywhere in a page, so results need checking."""
        return self.get(action="query", list="search", srsearch=query, srlimit=limit, format="json")["query"]["search"]

    def category_pages(self, category):
        """Titles of every mix page in a category. Each DJ, event and radio show has one, e.g. "Prospa" or "Essential Mix"."""
        titles, params = {}, {"action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}", "cmtype": "page", "cmlimit": 500, "format": "json"}
        while True:
            response = self.get(**params)
            titles.update((page["pageid"], page["title"]) for page in response["query"]["categorymembers"])
            if "continue" not in response:
                return titles
            params.update(response["continue"])

    def wikitext(self, title):
        page = self.get(action="query", prop="revisions", rvprop="content", rvslots="main", titles=title, format="json", formatversion=2)["query"]["pages"][0]
        return page["revisions"][0]["slots"]["main"]["content"]


def page_url(title):
    return "https://www.mixesdb.com/w/" + title.replace(" ", "_")
