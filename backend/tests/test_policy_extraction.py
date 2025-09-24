#!/usr/bin/env python3
"""
Test script for the improved policy extraction system with image fallback
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.graphDB_dataAccess import graphDBdataAccess

def test_policy_extraction():
    """
    Test the multi-level policy extraction system
    """
    print("🧪 Testing multi-level policy extraction system...")
    print("=" * 60)
    
    # Test cases
    test_cases = [
        "Asude Sitesi Yönetimi Ortak Alan Poliçesi.pdf",
        "Ayça Dinçkök Galata Residance D4 Konut 2020.pdf", 
        "Mehmet Yılmaz BMW X5 Kasko Zeyilname 2023.pdf",
        "Unknown Policy File.pdf"
    ]
    
    # Create database instance
    try:
        from neo4j import GraphDatabase
        from dotenv import load_dotenv
        
        # Load environment variables
        load_dotenv()
        
        # Create Neo4j driver
        driver = GraphDatabase.driver(
            os.getenv('NEO4J_URI'),
            auth=(os.getenv('NEO4J_USERNAME'), os.getenv('NEO4J_PASSWORD'))
        )
        
        # Create a simple graph wrapper for graphDBdataAccess
        class SimpleGraph:
            def __init__(self, driver):
                self._driver = driver
                self._database = os.getenv('NEO4J_DATABASE', 'neo4j')
            
            def query(self, query, parameters=None, session_params=None):
                with self._driver.session() as session:
                    result = session.run(query, parameters or {})
                    return [record.data() for record in result]
        
        # Create graph and database access instance
        graph = SimpleGraph(driver)
        db = graphDBdataAccess(graph)
        print("✅ Database connection established")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return
    
    # Test each case
    for i, test_filename in enumerate(test_cases, 1):
        print(f"\n📋 Test Case {i}: {test_filename}")
        print("-" * 50)
        
        try:
            # Test extraction
            result = db.extract_policy_info_from_filename(test_filename)
            
            print("✅ Extraction successful!")
            print("📄 Extracted Information:")
            
            for key, value in result.items():
                print(f"  • {key}: {value}")
                
            # Show extraction method
            method = result.get('extraction_method', 'unknown')
            if method == 'filename':
                print("🔍 Method: Filename analysis")
            elif method == 'image_vision':
                print("🖼️ Method: Image vision analysis")
            elif method == 'fallback':
                print("⚙️ Method: Fallback defaults")
            
        except Exception as e:
            print(f"❌ Extraction failed: {e}")
    
    print("\n" + "=" * 60)
    print("🏁 Test completed!")

def test_image_path_resolution():
    """
    Test if we can find page images for existing documents
    """
    print("\n🖼️ Testing image path resolution...")
    print("=" * 60)
    
    try:
        from neo4j import GraphDatabase
        from dotenv import load_dotenv
        
        # Load environment variables
        load_dotenv()
        
        # Create Neo4j driver
        driver = GraphDatabase.driver(
            os.getenv('NEO4J_URI'),
            auth=(os.getenv('NEO4J_USERNAME'), os.getenv('NEO4J_PASSWORD'))
        )
        
        # Create a simple graph wrapper for graphDBdataAccess
        class SimpleGraph:
            def __init__(self, driver):
                self._driver = driver
                self._database = os.getenv('NEO4J_DATABASE', 'neo4j')
            
            def query(self, query, parameters=None, session_params=None):
                with self._driver.session() as session:
                    result = session.run(query, parameters or {})
                    return [record.data() for record in result]
        
        # Create graph and database access instance
        graph = SimpleGraph(driver)
        db = graphDBdataAccess(graph)
        
        # Query for documents with page_images
        query = """
            MATCH (d:Document) 
            WHERE d.page_images IS NOT NULL 
            RETURN d.fileName, d.page_images 
            LIMIT 5
        """
        
        result = db.execute_query(query)
        
        if result:
            print("📚 Documents with page images found:")
            for row in result:
                file_name = row.get('d.fileName', 'Unknown')
                page_images = row.get('d.page_images', [])
                print(f"\n📄 {file_name}")
                
                if isinstance(page_images, list) and page_images:
                    first_image = page_images[0]
                    print(f"  🖼️ First page: {first_image}")
                    
                    # Check if file exists
                    if os.path.exists(first_image):
                        print("  ✅ Image file exists")
                        
                        # Test image-based extraction directly
                        print("  🤖 Testing image-based extraction...")
                        try:
                            image_info = db._extract_policy_info_from_image(first_image, file_name)
                            if image_info and image_info.get('customer_name'):
                                print("  ✅ Vision extraction successful!")
                                print("  🔍 Vision extraction result:")
                                for key, value in image_info.items():
                                    if value:  # Only show non-empty values
                                        print(f"    • {key}: {value}")
                            else:
                                print("  ⚠️ Vision extraction returned empty or incomplete")
                        except Exception as e:
                            print(f"  ❌ Vision extraction failed: {e}")
                    else:
                        print("  ❌ Image file not found locally, testing endpoint...")
                        # Test image-based extraction even if local file doesn't exist
                        print("  🤖 Testing image-based extraction via endpoint...")
                        try:
                            image_info = db._extract_policy_info_from_image(first_image, file_name)
                            if image_info and image_info.get('customer_name'):
                                print("  ✅ Vision extraction via endpoint successful!")
                                print("  🔍 Vision extraction result:")
                                for key, value in image_info.items():
                                    if value:  # Only show non-empty values
                                        print(f"    • {key}: {value}")
                            else:
                                print("  ⚠️ Vision extraction via endpoint returned empty or incomplete")
                        except Exception as e:
                            print(f"  ❌ Vision extraction via endpoint failed: {e}")
                else:
                    print("  ⚠️ No page images in list")
        else:
            print("❌ No documents with page images found")
            
    except Exception as e:
        print(f"❌ Image path test failed: {e}")

if __name__ == "__main__":
    test_policy_extraction()
    test_image_path_resolution()
