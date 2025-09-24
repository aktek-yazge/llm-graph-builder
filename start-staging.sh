#!/bin/bash
# Staging Environment Startup Script
# DevContainer servislerini kontrol eder ve staging'i başlatır

set -e

echo "🚀 Starting Staging Environment..."

# DevContainer network'ünü kontrol et
if ! docker network ls | grep -q "workspace_llm-graph-builder_net"; then
    echo "⚠️  DevContainer network bulunamadı!"
    echo "Önce DevContainer'ı başlatın:"
    echo "  docker-compose -f .devcontainer/docker-compose.dev.yml up -d"
    exit 1
fi

# Neo4j servisini kontrol et
if ! docker ps | grep -q "neo4j-service"; then
    echo "⚠️  Neo4j servisi çalışmıyor!"
    echo "DevContainer servislerini başlatın:"
    echo "  docker-compose -f .devcontainer/docker-compose.dev.yml up -d neo4j"
else
    echo "✅ Neo4j servisi bulundu: $(docker ps --format '{{.Names}}' | grep neo4j)"
fi

# Qdrant servisini kontrol et  
if ! docker ps | grep -q "qdrant-service"; then
    echo "⚠️  Qdrant servisi çalışmıyor!"
    echo "DevContainer servislerini başlatın:"
    echo "  docker-compose -f .devcontainer/docker-compose.dev.yml up -d qdrant"
else
    echo "✅ Qdrant servisi bulundu: $(docker ps --format '{{.Names}}' | grep qdrant)"
fi

# Network bağlantısını test et
echo "🔗 Network bağlantıları kontrol ediliyor..."

# Staging'i başlat
echo "🚀 Staging servislerini başlatıyor..."
docker-compose -f docker-compose.staging.yml up --build -d

# Servis durumunu kontrol et
echo "📊 Staging servisleri:"
docker-compose -f docker-compose.staging.yml ps

echo ""
echo "✅ Staging environment hazır!"
echo ""
echo "🌐 Access Points:"
echo "  Backend API:  http://localhost:8001"
echo "  Frontend:     http://localhost:8081"  
echo "  Neo4j:        http://localhost:7474 (shared with dev)"
echo "  Qdrant:       http://localhost:6333 (shared with dev)"
echo ""
echo "📊 Logs:"
echo "  docker-compose -f docker-compose.staging.yml logs -f"
echo ""
echo "🛑 Stop:"
echo "  docker-compose -f docker-compose.staging.yml down"