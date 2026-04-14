while true; do 
  echo -n "[$(date +%T)] Current Embeddings: "
  docker exec immich_postgres psql -U postgres -d immich -t -c "SELECT count(*) FROM smart_search;" | tr -d '[:space:]'
  sleep 10
done