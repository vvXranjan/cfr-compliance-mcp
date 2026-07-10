import asyncio

from cfr_compliance_mcp.clients.ecfr_client import create_ecfr_client
from cfr_compliance_mcp.parsing.xml_parser import parse_regulation_xml


async def main():
    http_client, ecfr_client = create_ecfr_client()

    await http_client.start()

    try:
        xml = await ecfr_client.retrieve_section(
            title=40,
            part="261",
            section="10",
            date="2026-07-08",
        )

        result = parse_regulation_xml(
            xml,
            title=40,
            part="261",
            section="10",
            date="2026-07-08",
        )

        print(result)

    finally:
        await http_client.aclose()


asyncio.run(main())