#!/bin/bash

# Graph-Code Setup Script
# This script sets up and runs Graph-Code on this repository to create a knowledge graph

set -e  # Exit on error

echo "========================================="
echo "Graph-Code Setup and Execution"
echo "========================================="
echo ""

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "  $1"
}

# =========================================
# Step 1: Check Prerequisites
# =========================================
echo "Step 1: Checking prerequisites..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    print_error "uv is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
print_success "uv is installed"

# Check if docker is installed
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker Desktop or Colima."
    exit 1
fi
print_success "Docker is installed"

# Check if Docker is running
if ! docker info &> /dev/null; then
    print_error "Docker is not running. Please start Docker Desktop or run 'colima start'"
    exit 1
fi
print_success "Docker is running"

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    print_error "docker-compose is not installed"
    exit 1
fi
print_success "docker-compose is installed"

echo ""

# =========================================
# Step 2: Setup Environment Configuration
# =========================================
echo "Step 2: Setting up environment configuration..."

if [ ! -f .env ]; then
    print_info "Creating .env file from .env.example"
    cp .env.example .env
    print_success ".env file created"
    print_warning "Note: .env is configured for Ollama (local models) by default"
    print_info "To use cloud providers (Google/OpenAI), edit .env and add your API keys"
else
    print_success ".env file already exists"
fi

echo ""

# =========================================
# Step 3: Start Memgraph Database
# =========================================
echo "Step 3: Starting Memgraph database..."

# Check if Memgraph is already running
if docker-compose ps | grep -q "memgraph.*Up"; then
    print_success "Memgraph is already running"
else
    print_info "Starting Memgraph and Memgraph Lab with docker-compose..."
    docker-compose up -d

    # Wait for Memgraph to be ready
    print_info "Waiting for Memgraph to be ready..."
    sleep 5

    # Check if containers are running
    if docker-compose ps | grep -q "memgraph.*Up"; then
        print_success "Memgraph started successfully"
        print_info "Memgraph Lab available at: http://localhost:3000"
        print_info "Memgraph database running on: localhost:7687"
    else
        print_error "Failed to start Memgraph"
        exit 1
    fi
fi

echo ""

# =========================================
# Step 4: Parse Repository into Graph
# =========================================
echo "Step 4: Parsing repository into knowledge graph..."

REPO_PATH=$(pwd)
print_info "Repository path: $REPO_PATH"

# Ask user if they want to clean the database (only if it might have existing data)
if docker-compose ps | grep -q "memgraph.*Up"; then
    read -p "Clean existing graph data? (y/n, default: y): " CLEAN_DB
    CLEAN_DB=${CLEAN_DB:-y}

    if [[ $CLEAN_DB == "y" || $CLEAN_DB == "Y" ]]; then
        print_info "Parsing repository with --clean flag (will remove existing data)..."
        uv run cgr start --repo-path "$REPO_PATH" --update-graph --clean
    else
        print_info "Parsing repository without --clean flag (will merge with existing data)..."
        uv run cgr start --repo-path "$REPO_PATH" --update-graph
    fi
else
    print_info "Parsing repository..."
    uv run cgr start --repo-path "$REPO_PATH" --update-graph --clean
fi

print_success "Repository parsed successfully"

echo ""

# =========================================
# Step 5: Export Graph to JSON
# =========================================
echo "Step 5: Exporting graph to JSON..."

OUTPUT_FILE="$REPO_PATH/code-graph-rag-graph.json"
print_info "Exporting to: $OUTPUT_FILE"

uv run cgr export -o "$OUTPUT_FILE"

if [ -f "$OUTPUT_FILE" ]; then
    FILE_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
    print_success "Graph exported successfully (Size: $FILE_SIZE)"
else
    print_error "Failed to export graph"
    exit 1
fi

echo ""

# =========================================
# Summary
# =========================================
echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
print_success "Knowledge graph created successfully!"
echo ""
echo "Next steps:"
echo ""
echo "1. Explore in Memgraph Lab (Visual Interface):"
print_info "   Open http://localhost:3000 in your browser"
print_info "   Use the Cypher queries from CYPHER.md"
echo ""
echo "2. Explore the JSON export:"
print_info "   File: $OUTPUT_FILE"
print_info "   View with: python -m json.tool $OUTPUT_FILE | less"
echo ""
echo "3. Interactive query mode (requires LLM setup):"
print_info "   Configure .env with API keys for Google/OpenAI, OR"
print_info "   Install Ollama: curl -fsSL https://ollama.ai/install.sh | sh"
print_info "   Then run: uv run cgr start --repo-path $REPO_PATH"
echo ""
echo "4. Stop Memgraph when done:"
print_info "   docker-compose down"
echo ""
print_success "Happy exploring! 🚀"
