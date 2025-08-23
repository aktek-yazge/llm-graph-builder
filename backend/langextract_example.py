"""Simple demo that imports langextract package and demonstrates        # Show available providers
        print(f"\n🔌 Testing provider access:")
        try:
            print(f"   - Providers module: {providers}")
            # Try to list some common attributes
            provider_attrs = [attr for attr in dir(providers) if not attr.startswith('_')]
            print(f"   - Provider attributes: {provider_attrs[:10]}")
        except Exception as e:
            print(f"   Could not list providers: {e}")sage.

Langextract is installed in editable mode from the submodule, so it behaves
like a normal pip package while allowing development changes.
"""

from __future__ import annotations

def main() -> int:
    try:
        # Import langextract and core modules directly
        import langextract as lx
        from langextract.extraction import extract
        from langextract import data, schema, providers
        
        print("✅ Successfully imported langextract and core modules")
        print(f"📦 Extract function: {extract}")
        print(f"📦 Data module: {data}")
        print(f"📦 Schema module: {schema}")
        print(f"📦 Providers module: {providers}")

        # Demonstrate basic schema-based extraction
        sample_text = """
        John Smith is a 35-year-old software engineer living in San Francisco.
        He works at Google and has 10 years of experience in machine learning.
        His email is john.smith@example.com and his phone number is (555) 123-4567.
        """

        prompt = "Extract person information including name, age, occupation, location, company, and contact details"
        
        print("\n🔄 Attempting basic langextract functionality...")
        print(f"📝 Input text: {sample_text.strip()}")
        
        # Try basic extract functionality
        try:
            # Test basic extract with minimal config
            print("🧪 Testing minimal extract call...")
            
            result = extract(
                text_or_documents=sample_text,
                prompt_description=prompt,
            )
            print("🎉 Extraction result:", result)
            
        except Exception as extraction_error:
            print(f"⚠️  Extraction failed (expected without model config): {type(extraction_error).__name__}: {extraction_error}")
            print("💡 This is normal - langextract requires a configured LLM provider")
            print("\n💡 To use extraction, configure a provider like:")
            print("   - Set OPENAI_API_KEY and add model_id='gpt-4o-mini'")
            print("   - Set GOOGLE_API_KEY and add model_id='gemini-1.5-flash'") 
            print("   - Run Ollama locally and add model_id='ollama/llama3'")
            print("\n📚 Example usage with OpenAI:")
            print("   export OPENAI_API_KEY='your-key'")
            print("   result = extract(text, prompt, model_id='gpt-4o-mini')")

        # Show available data classes
        print(f"\n📊 Available data classes:")
        try:
            print(f"   - ExampleData: {data.ExampleData}")
            print(f"   - Extraction: {data.Extraction}")
            print(f"   - FormatType: {data.FormatType}")
        except Exception as e:
            print(f"   Could not access data classes: {e}")

        # Show available providers
        print(f"\n� Available providers:")
        try:
            provider_list = providers.list_providers()
            for provider in provider_list:
                print(f"   - {provider}")
        except Exception as e:
            print(f"   Could not list providers: {e}")
            
        return 0
        
    except ImportError as e:
        print("❌ Failed to import langextract:", e)
        print("💡 Make sure langextract is installed: pip install -e backend/langextract")
        return 1
    except Exception as e:
        print("❌ Error running langextract demo:", type(e).__name__, e)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
