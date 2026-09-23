library identifier: 'JenkinsPipelineUtils', changelog: false

// FieldnotesApp's pipeline: the uv workspace's own gates, then the one image both services ship in,
// then the HelmCharts target-state deploy that rolls it out.
//
// The validate stage runs in `modern-app-dev` for the same reasons KubeCoder's does: it is the
// agent image that carries uv, and it carries git — which is not incidental here, because the
// store's suites (api/tests, eval/tests) drive a real `git` against bare repos in tmp_path rather
// than a fake. `registry:5000/python` would run the unit tests and fail those.
podTemplate(inheritFrom: 'jenkins-agent kaniko', containers: [
    containerTemplates.k8s('k8s'),
    containerTemplates.modern_app_dev('modern-app-dev'),
]) {
    node(POD_LABEL) {
        stage('Cloning repo') {
            checkout scm
        }

        // The three verbs of `.kubecoder/project.yaml`'s `root` component, run here directly rather
        // than through `kc project`: setup, lint, test. --all-packages on the sync because the
        // workspace root is package=false, so a plain `uv sync` installs only the root dev group
        // (pytest/ruff) and none of the members; --no-sync on each `uv run` keeps that full env,
        // where a re-sync would prune it back. pytest's testpaths covers every member, so one run
        // is the whole suite.
        stage('Validate (lint + tests)') {
            container('modern-app-dev') {
                sh 'uv sync --all-packages --frozen'
                sh 'uv run --no-sync ruff check .'
                sh 'uv run --no-sync ruff format --check .'
                sh 'uv run --no-sync pytest'
            }
        }

        // One image, two entry points (Dockerfile): the pod runs it as `api` and as `mcp`. The
        // context is the repo root, which is the uv workspace root.
        stage('Build fieldnotes') {
            container('kaniko') {
                helmCharts.kaniko([
                    "registry:5000/fieldnotes:${currentBuild.number}",
                    "registry:5000/fieldnotes:latest",
                ])
            }
        }

        // Push-to-deploy: trigger the HelmCharts target-state pipeline so the freshly built image
        // rolls out. The `fieldnotes` release pins `:latest` and redeploys on the digest move.
        // The build hands its image to Argo CD by pinning it in the deploy repo (argo-cd D53);
        // Argo syncs the commit. HelmCharts no longer deploys this app.
        stage('Write image pins') {
            container('k8s') {
                cicd.writeVersionPins(repo: 'pvginkel/FieldnotesDeploy', pins: [
                    'config/prd/values.yaml': ['images.fieldnotes': ":${currentBuild.number}"]
                ])
            }
        }
    }
}
