# Artifact Hub publishing

The package is an OCI Helm chart. Publish each SemVer chart version to one
public OCI repository and register that exact repository in Artifact Hub:

```sh
helm package . --destination dist
helm push dist/re8ch-advanced-fabric-0.3.0.tgz \
  oci://ghcr.io/re8ch/charts
```

The resulting Artifact Hub repository URL is:

```text
oci://ghcr.io/re8ch/charts/re8ch-advanced-fabric
```

Artifact Hub requires one repository registration per OCI chart. After the
repository is created in the Artifact Hub control panel, obtain its repository
ID and create `artifacthub-repo.yml` without committing credentials:

```yaml
repositoryID: <artifact-hub-repository-id>
owners:
  - name: <artifact-hub-owner-name>
    email: <artifact-hub-account-email>
```

Publish that metadata with the official OCI media types and special tag:

```sh
oras push ghcr.io/re8ch/charts/re8ch-advanced-fabric:artifacthub.io \
  --config /dev/null:application/vnd.cncf.artifacthub.config.v1+yaml \
  artifacthub-repo.yml:application/vnd.cncf.artifacthub.repository-metadata.layer.v1.yaml
```

Signing metadata is intentionally absent until a public verification key and
release signing workflow exist. Do not claim Verified Publisher or signed
status before those gates are complete.
