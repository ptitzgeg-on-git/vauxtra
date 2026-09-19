"""Every endpoint that creates a resource answers 201, and says so in the schema.

`POST /api/settings/api-keys` answered 200 while the eleven other creation routes answered
201. Nothing broke: both clients accept any 2xx, so the inconsistency was invisible from the
interface and from the MCP bridge. It was visible exactly where it mattered least to us and
most to someone else -- in the published schema, which is what a third-party client reads to
learn what a successful creation looks like.

The assertions run against `app.openapi()` rather than against the decorators, because the
schema is the contract. A route could declare `status_code=201` and still be documented
otherwise; asking the schema removes that gap.
"""

import unittest

from app import main as app_main

# The POST routes that create a resource and return its identifier. Kept explicit rather
# than guessed from the path, because "POST to a collection" is not a reliable rule: several
# POST routes here are actions on an existing object, not creations.
CREATION_ENDPOINTS = [
    "/api/docker/endpoints",
    "/api/domains",
    "/api/environments",
    "/api/providers",
    "/api/providers/{pid}/dns-records",
    "/api/providers/{pid}/proxy-hosts",
    "/api/services",
    "/api/settings/api-keys",
    "/api/tags",
    "/api/templates",
    "/api/webhooks",
]


class CreationStatusCodes(unittest.TestCase):
    def setUp(self):
        self.paths = app_main.app.openapi()["paths"]

    def test_every_creation_endpoint_declares_201(self):
        for path in CREATION_ENDPOINTS:
            with self.subTest(path=path):
                self.assertIn(path, self.paths, f"{path} is no longer in the schema")
                post = self.paths[path].get("post")
                self.assertIsNotNone(post, f"{path} no longer answers POST")
                self.assertIn(
                    "201",
                    post["responses"],
                    f"POST {path} creates a resource and must answer 201, not "
                    f"{sorted(post['responses'])}",
                )

    def test_the_list_holds_every_route_that_answers_201(self):
        """The guard in the other direction, so the list above cannot quietly rot.

        It catches a creation route that is removed or renamed. It does not catch a NEW
        creation route added with 200: nothing in the schema distinguishes a creation from
        an action, which is why the list is written by hand. That one stays on review.
        """
        declared = {
            path
            for path, verbs in self.paths.items()
            if "201" in (verbs.get("post") or {}).get("responses", {})
        }
        self.assertEqual(
            declared,
            set(CREATION_ENDPOINTS),
            "a route started or stopped answering 201 without this list being updated",
        )


if __name__ == "__main__":
    unittest.main()
