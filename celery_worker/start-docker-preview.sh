#!/bin/bash
# Start Celery Workers in Docker
# Uses .preview.env for Docker environment (rabbitmq-dev, postgres-dev hostnames)
#
# Usage: ./start-docker.sh [service_name]
# Examples:
#   ./start-docker.sh              # Start all services
#   ./start-docker.sh main_worker  # Start only main worker
#   ./start-docker.sh flower       # Start only flower

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Starting Celery Workers (Docker)..."
echo "   Environment: .preview.env"
echo ""

if [ -n "$1" ]; then
    echo "   Service: $1"
    docker compose --env-file .preview.env up -d "$1"
else
    echo "   Services: main_worker, db_writer, neo4j_writer, flower"
    docker compose --env-file .preview.env up -d
fi

echo ""
echo "✅ Started!"
echo ""
echo "📊 Status:"
docker compose -f docker-compose.yml ps
echo ""
echo "🌸 Flower Dashboard: http://localhost:5556"
echo "🐰 RabbitMQ Management: http://localhost:15672 (guest/guest)"
echo ""
echo "📋 Useful commands:"
echo "  docker compose -f docker-compose.yml logs -f              # View all logs"
echo "  docker compose -f docker-compose.yml logs -f main_worker  # View main worker logs"
echo "  ./stop-docker.sh                    # Stop all workers"

