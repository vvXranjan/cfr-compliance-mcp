from agent.compliance_agent import evaluate_compliance


def main():

    result = evaluate_compliance(
        clause_title="Environmental Waste Disposal",
        clause_text="""
        Contractor shall properly dispose hazardous waste
        according to EPA requirements and maintain records.
        """,
        cfr_citation="40 CFR 257.3",
        cfr_text="""
        Solid waste disposal facilities and practices
        must comply with environmental protection criteria.
        """
    )

    print("=" * 60)
    print("COMPLIANCE RESULT")
    print("=" * 60)

    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()