#!/bin/bash
# Docker Log Sync Script
# Syncs Docker container logs to host with container ID-based folders
# Handles log rotation when files exceed 500MB
# Run this script periodically (via cron or systemd timer)

LOG_BASE_DIR="${DOCKER_LOG_DIR:-~/docker-logs}"
MAX_FILE_SIZE=524288000  # 500MB in bytes
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Expand ~ to home directory
LOG_BASE_DIR="${LOG_BASE_DIR/#\~/$HOME}"

# Function to sync logs for a container
sync_container_logs() {
    local container_name=$1
    local container_id=$(docker ps -aqf "name=^${container_name}$" | head -n 1)
    
    if [ -z "$container_id" ]; then
        echo "⚠️  Container not found or not running: $container_name"
        return 1
    fi
    
    # Create directory structure: ~/docker-logs/<container-name>/<container-id>/
    local log_dir="${LOG_BASE_DIR}/${container_name}/${container_id}"
    mkdir -p "$log_dir"
    
    # Get Docker log file path
    local docker_log_path="/var/lib/docker/containers/${container_id}/${container_id}-json.log"
    
    # Check if we have access to Docker log file (requires root or docker group)
    if [ ! -r "$docker_log_path" ]; then
        # Fallback: Use docker logs command
        local current_log="${log_dir}/container-${container_id}-current.log"
        docker logs --tail 10000 "$container_id" >> "$current_log" 2>&1
        echo "📋 Synced logs for $container_name ($container_id) via docker logs -> $log_dir"
        return 0
    fi
    
    # Check if log file exists and get its size
    if [ -f "$docker_log_path" ]; then
        local file_size=$(stat -f%z "$docker_log_path" 2>/dev/null || stat -c%s "$docker_log_path" 2>/dev/null)
        local current_log="${log_dir}/container-${container_id}-current.log"
        
        # If file exceeds max size, rotate
        if [ "$file_size" -gt "$MAX_FILE_SIZE" ]; then
            local rotated_file="${log_dir}/container-${container_id}-${TIMESTAMP}.log"
            echo "📦 Rotating log file (size: $file_size bytes) -> $rotated_file"
            cp "$docker_log_path" "$rotated_file"
            # Keep only last 10 rotated files
            ls -t "${log_dir}/container-${container_id}-"*.log 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null
        fi
        
        # Sync current log (append new content)
        if [ -f "$current_log" ]; then
            # Get the size of current log to append only new content
            local current_size=$(stat -f%z "$current_log" 2>/dev/null || stat -c%s "$current_log" 2>/dev/null || echo 0)
            if [ "$file_size" -gt "$current_size" ]; then
                tail -c +$((current_size + 1)) "$docker_log_path" >> "$current_log"
            fi
        else
            # First sync - copy entire file
            cp "$docker_log_path" "$current_log"
        fi
        
        echo "✅ Synced logs for $container_name ($container_id) -> $log_dir (size: $file_size bytes)"
    else
        echo "⚠️  Log file not found: $docker_log_path"
    fi
}

# Main execution
if [ $# -eq 0 ]; then
    echo "Usage: $0 <container-name1> [container-name2] ..."
    echo "Or set DOCKER_LOG_DIR environment variable to change log directory (default: ~/docker-logs)"
    echo ""
    echo "To sync all containers from docker-compose:"
    echo "  $0 backend-prod frontend-prod qdrant-service-prod neo4j-service-prod neo4j-service-prod-2"
    exit 1
fi

echo "📋 Docker Log Sync"
echo "   Log directory: $LOG_BASE_DIR"
echo "   Max file size: 500MB"
echo "   Timestamp: $TIMESTAMP"
echo ""

# Sync logs for each container
for container_name in "$@"; do
    sync_container_logs "$container_name"
done

echo ""
echo "✅ Log sync completed!"


