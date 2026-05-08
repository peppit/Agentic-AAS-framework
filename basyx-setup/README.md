# BaSyx Setup
This is your BaSyx setup. To run the BaSyx containers, you need to have Docker installed on your device.

## Configuration (secrets)

Secrets are not stored in source control. Before starting the stack, create a `.env` file in this folder by copying the example:

```
copy .env.example .env
```

Then edit `.env` and fill in the values:

| Variable | Description |
|---|---|
| `OPCUA_ACCESS_CODE` | Integer access code written to the OPC UA `AccessCode` node |
| `MONGO_PASSWORD` | Password for the MongoDB `mongoAdmin` user |

The `.env` file is excluded from git via `.gitignore` and will never be committed.

## How to run the BaSyx containers
1. Clone or extract the repository on your device.
2. Create and populate `.env` as described above.
3. Open a terminal and navigate to the folder.
4. Run the following command to start the BaSyx containers:
```
docker-compose up -d
```

## Access the BaSyx containers
- AAS Environment: [http://localhost:8081](http://localhost:8081)
- AAS Registry: [http://localhost:8082](http://localhost:8082)
- Submodel Registry: [http://localhost:8083](http://localhost:8083)
- AAS Discovery: [http://localhost:8084](http://localhost:8084)
- AAS Web UI: [http://localhost:3000](http://localhost:3000)
- OPC UA Operation Service: [http://localhost:8087](http://localhost:8087)

## Include your own Asset Administration Shells
To include your own Asset Administration Shells, you can either put them in the `aas` folder or upload them via the AAS Web UI.