# from pathlib import Path

# from contract_parser import ContractParser


# def main():

#     pdf = Path("contracts/sample_contract.pdf")

#     parser = ContractParser(pdf)

#     text = parser.extract_text()

#     print("=" * 60)
#     print("Contract Summary")
#     print("=" * 60)

#     print(f"Pages: {parser.page_count()}")
#     print(f"Characters: {len(text)}")

#     print("\nFirst 500 characters:\n")

#     print(text[:500])


# if __name__ == "__main__":
#     main()

from pathlib import Path

from .contract_parser import ContractParser, split_into_clauses


def main():

    pdf = Path("contracts/sample_contract.pdf")

    parser = ContractParser(pdf)

    text = parser.extract_text()

    print("=" * 60)
    print("Contract Summary")
    print("=" * 60)

    print(f"Pages: {parser.page_count()}")
    print(f"Characters: {len(text)}")

    print("\nFirst 500 characters:\n")

    print(text[:500])

    clauses = split_into_clauses(text)

    print(f"\nFound {len(clauses)} clauses")

    for number, clause in enumerate(clauses, start=1):
        print("=" * 60)
        print(f"Clause {number}")
        print(clause.title)
        print(f"Length: {len(clause.text)} characters")
        print("-" * 60)
        print(clause.text[:400])


if __name__ == "__main__":
    main()