FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm install

COPY . .

# Expose Vite's default port
EXPOSE 5173

# Run Vite allowing external connections
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
