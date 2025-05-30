# Synapse Delete Room Rest API

This module expose and endpoint for room admins (or room members with highest power level) to kick everyone out and leave the room.

## Usage

Send a `POST` request to `/_synapse/client/pangea/v1/delete_room` with JSON body `{room_id: string}`. Response `200 OK` format: `{ message: "Deleted" }`.

Requester must be member of the room and have the highest power level of the room to perform this request.

## Installation

From the virtual environment that you use for Synapse, install this module with:
```shell
pip install path/to/synapse-delete-room-rest-api
```
(If you run into issues, you may need to upgrade `pip` first, e.g. by running
`pip install --upgrade pip`)

Then alter your homeserver configuration, adding to your `modules` configuration:
```yaml
modules:
  - module: synapse_delete_room_rest_api.SynapseDeleteRoomRestAPI
    config:
      delete_room_requests_per_burst: 10
      delete_room_burst_duration_seconds: 60
```


## Development

In a virtual environment with pip ≥ 21.1, run
```shell
pip install -e .[dev]
```

To run the unit tests, you can either use:
```shell
tox -e py
```
or
```shell
trial tests
```

To run the linters and `mypy` type checker, use `./scripts-dev/lint.sh`.


## Releasing

The exact steps for releasing will vary; but this is an approach taken by the
Synapse developers (assuming a Unix-like shell):

 1. Set a shell variable to the version you are releasing (this just makes
    subsequent steps easier):
    ```shell
    version=X.Y.Z
    ```

 2. Update `setup.cfg` so that the `version` is correct.

 3. Stage the changed files and commit.
    ```shell
    git add -u
    git commit -m v$version -n
    ```

 4. Push your changes.
    ```shell
    git push
    ```

 5. When ready, create a signed tag for the release:
    ```shell
    git tag -s v$version
    ```
    Base the tag message on the changelog.

 6. Push the tag.
    ```shell
    git push origin tag v$version
    ```

 7. If applicable:
    Create a *release*, based on the tag you just pushed, on GitHub or GitLab.

 8. If applicable:
    Create a source distribution and upload it to PyPI:
    ```shell
    python -m build
    twine upload dist/synapse_delete_room_rest_api-$version*
    ```
