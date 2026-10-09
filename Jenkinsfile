// Builds and pushes the three dynamiq-console images (ai, console-api, web) and applies
// the k8s manifests to the ai-hub-advws namespace. Modeled on operations-advws's existing
// Jenkins job, which this repo has no visibility into (no Jenkinsfile/CI config checked
// into that repo either — its pipeline lives entirely in Jenkins' own job config).
//
// REGISTRY confirmed by inspecting existing deployments in cdi-advws (e.g.
// fastapi-dynamiq's image is cr.advws.com/fastapi-dynamiq:c3fb4d4) — every pod there
// pulls via a dockersecret imagePullSecret. ai-hub-advws is a brand-new, separate
// namespace though, so that imagePullSecret does NOT already exist there — infra needs
// to create/copy it in (see deploy/k8s/HANDOFF.md) before any image pull will work. The
// two credentials ids below are still placeholders — infra needs to fill in whichever
// Jenkins credential actually holds push access to cr.advws.com and the kubeconfig/token
// with write access to ai-hub-advws.
def REGISTRY = 'cr.advws.com'
def REGISTRY_CREDENTIALS_ID = 'TODO-registry-credentials-id'
def KUBECONFIG_CREDENTIALS_ID = 'TODO-kubeconfig-credentials-id'

pipeline {
    agent any

    environment {
        IMAGE_TAG = "${env.GIT_COMMIT.take(12)}"
    }

    stages {
        stage('Build images') {
            parallel {
                stage('ai') {
                    steps {
                        dir('ai') {
                            sh "docker build -f ../deploy/docker/ai/Dockerfile -t ${REGISTRY}/dynamiq-console/ai:${IMAGE_TAG} ."
                        }
                    }
                }
                stage('console-api') {
                    steps {
                        dir('console-api') {
                            sh "docker build -f ../deploy/docker/console-api/Dockerfile -t ${REGISTRY}/dynamiq-console/console-api:${IMAGE_TAG} ."
                        }
                    }
                }
                stage('web') {
                    steps {
                        dir('web') {
                            sh "docker build -f ../deploy/docker/web/Dockerfile -t ${REGISTRY}/dynamiq-console/web:${IMAGE_TAG} ."
                        }
                    }
                }
            }
        }

        stage('Push images') {
            steps {
                withCredentials([usernamePassword(credentialsId: REGISTRY_CREDENTIALS_ID, usernameVariable: 'REG_USER', passwordVariable: 'REG_PASS')]) {
                    sh "echo \$REG_PASS | docker login ${REGISTRY} -u \$REG_USER --password-stdin"
                    sh "docker push ${REGISTRY}/dynamiq-console/ai:${IMAGE_TAG}"
                    sh "docker push ${REGISTRY}/dynamiq-console/console-api:${IMAGE_TAG}"
                    sh "docker push ${REGISTRY}/dynamiq-console/web:${IMAGE_TAG}"
                }
            }
        }

        stage('Deploy') {
            steps {
                withKubeConfig([credentialsId: KUBECONFIG_CREDENTIALS_ID]) {
                    sh """
                        kubectl -n ai-hub-advws set image deployment/ai-runtime ai=${REGISTRY}/dynamiq-console/ai:${IMAGE_TAG}
                        kubectl -n ai-hub-advws set image deployment/ai-gateway ai=${REGISTRY}/dynamiq-console/ai:${IMAGE_TAG}
                        kubectl -n ai-hub-advws set image deployment/ai-worker ai=${REGISTRY}/dynamiq-console/ai:${IMAGE_TAG}
                        kubectl -n ai-hub-advws set image deployment/console-api console-api=${REGISTRY}/dynamiq-console/console-api:${IMAGE_TAG}
                        kubectl -n ai-hub-advws set image deployment/web web=${REGISTRY}/dynamiq-console/web:${IMAGE_TAG}
                    """
                }
            }
        }
    }
}
