from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_docker_context_keeps_frontend_sources_and_drops_their_dependencies(self) -> None:
        """镜像在构建阶段编前端，因此上下文要有源码，node_modules 与 dist 由镜像自己产出。"""
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertNotIn("admin-ui/src", dockerignore)
        self.assertIn("admin-ui/node_modules", dockerignore)
        self.assertIn("admin-ui/dist", dockerignore)

    def test_image_builds_the_admin_client_and_serves_it_from_the_same_port(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("RUN npm run build", dockerfile)
        self.assertIn("COPY --from=admin-ui /ui/dist ./admin-ui/dist", dockerfile)
        self.assertEqual(dockerfile.count("EXPOSE"), 1)


if __name__ == "__main__":
    unittest.main()
